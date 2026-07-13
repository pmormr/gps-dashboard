"""Pi-side fridge reader: Dometic CFX3 DDMP over WiFi → MQTT publisher.

Bridges the van's Dometic CFX3 75DZ fridge into the sensor platform. The wire
protocol (DDMP framing, topic registry, codecs, and the session client) lives in
``common/ddmp.py`` — shared with the control routes; this module owns the *poll
policy*: what to subscribe each cycle and how the snapshot reaches the bus. Each
cycle polls one snapshot and publishes it to ``sensors/<node>/fridge`` on the Pi
broker, where ``mqtt-ingest`` writes it into ``fridge_readings`` — see
``mqttbus/ingest.py`` and ``api/sensor_schema.py``.

Poll, don't hold: the fridge serves one app client at a time, so each cycle is a
short connect→subscribe→collect→disconnect and the phone app keeps working between
polls. Like the Victron reader, this one owns its retained ``status`` flag
(``announce_online=False``): a failed poll (fridge WiFi off, out of range) flips the
stream ``offline`` rather than leaving it online with no data.

The stored column set is ``common.ddmp.TOPICS`` plus ``dc_current_a`` — the live
DC-draw signal lifted from the fridge's own hour-span history each cycle. The full
history arrays (hour/day/week, ``HISTORY_EVERY`` cadence) ride the same payload as
flattened ``fridge_history`` UPSERT rows (bucket semantics: ``reference/
cfx3-ddmp.md``). The DDMP error topics (NTC/compressor/fan) are deliberately
absent: the reference repo's params for them are self-described mock-broker
values, so subscribing would be guesswork. The four alert topics are real.

Run::

    uv run sensors/fridge_reader.py --fake --node van   # synthetic fridge, real sink
    uv run sensors/fridge_reader.py --node van          # real fridge (CFX_HOST)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from common.ddmp import (
    HISTORY_BUCKET_S,
    HISTORY_BUCKET_TICKS,
    HISTORY_PARAMS,
    TOPICS,
    DdmpClient,
    DdmpError,
    decode_history,
    decode_value,
    fridge_host,
    fridge_port,
)
from common.timefmt import canonical_timestamp, now_canonical
from sensors.runner import (
    Heartbeat,
    add_publisher_args,
    bounded_walk,
    publisher_session,
)

SENSOR_TYPE = 'fridge'

# Fridge temps are slow-moving and every poll briefly claims the fridge's single
# app slot, so poll sparsely.
PUBLISH_INTERVAL_S = 60.0
# Whole-poll budget: a wedged exchange abandons the cycle rather than stalling
# the loop (the next cycle reconnects fresh).
POLL_DEADLINE_S = 15.0

# History poll cadence (bucket widths live in common.ddmp.HISTORY_BUCKET_S):
# hour is re-polled every cycle (its front bucket is the live DC-draw signal);
# day/week change slowly, so they ride every 15th cycle to keep sessions short
# (slot courtesy: the poll, the app, and control writes share the fridge's
# single client slot).
HISTORY_EVERY: dict[str, int] = {'hour': 1, 'day': 15, 'week': 15}

#: snapshot columns: every DDMP scalar topic plus the history-derived DC draw.
FRIDGE_COLUMNS: list[str] = [*TOPICS, 'dc_current_a']


@dataclass
class FridgePoll:
    """One poll's result: the flat snapshot plus flattened history bucket rows.

    Attributes:
        values: Column → value over :data:`FRIDGE_COLUMNS` (None = unanswered).
        history: ``{span, bucket_ts, dc_current_a}`` rows for the spans polled
            this cycle (ingest UPSERTs them into ``fridge_history``).
    """

    values: dict[str, float | int | None]
    history: list[dict[str, object]] = field(default_factory=list)


class FridgeSource(Protocol):
    """A fridge poll source (real or fake) for :func:`publish_loop`."""

    def read(self) -> FridgePoll | None:
        """Return one poll, or None when the fridge is unreachable."""
        ...


def bucket_timestamp(epoch_s: float) -> str:
    """Render a bucket-start epoch as a canonical ms-UTC timestamp."""
    return canonical_timestamp(datetime.fromtimestamp(epoch_s, UTC).isoformat())


def flatten_history(
    span: str, buckets: list[float], tail: int | None, poll_epoch_s: float
) -> list[dict[str, object]]:
    """Flatten one decoded history array into UPSERT rows with absolute bucket times.

    The fridge's buckets are sliding, internally-clocked windows (probed:
    256 ticks each, the frame's tail byte is the tick counter), so absolute
    times are anchored on the tail: the in-progress bucket (index 0) started
    ``tail`` ticks before the poll, and each earlier bucket steps back one
    width. Starts snap to a quarter-width grid so consecutive polls key the
    same rows — the in-progress bucket's value converges in place until it
    rolls, and a rolled bucket keeps its key at index 1. The snap also absorbs
    tick jitter and small fridge-clock drift (≤ width/8 mislabeling worst case).

    Args:
        span: A :data:`HISTORY_BUCKET_S` span.
        buckets: Decoded per-bucket average DC amps, newest first.
        tail: The frame's tick counter (None decodes as 0 — phase unknown).
        poll_epoch_s: Unix time of the poll.

    Returns:
        One ``{span, bucket_ts, dc_current_a}`` row per bucket.
    """
    width = HISTORY_BUCKET_S[span]
    tick = width / HISTORY_BUCKET_TICKS
    grid = width / 4
    newest_start = round((poll_epoch_s - (tail or 0) * tick) / grid) * grid
    return [
        {'span': span, 'bucket_ts': bucket_timestamp(newest_start - i * width), 'dc_current_a': v}
        for i, v in enumerate(buckets)
    ]


class FridgeSensor:
    """Polls one snapshot (plus due history spans) per :meth:`read` call.

    Each poll is a fresh :class:`~common.ddmp.DdmpClient` session: PING, a
    SUBSCRIBE per scalar column, the history spans due this cycle
    (:data:`HISTORY_EVERY`), then a drain for in-flight publishes. Unanswered
    topics come back None — their columns store NULL for that cycle rather than
    failing the poll — and a failed history read just omits that span's rows.
    """

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._cycle = 0

    def read(self) -> FridgePoll | None:
        """Return one poll, or None if the fridge is unreachable.

        Returns:
            The poll (every :data:`FRIDGE_COLUMNS` key present, None for topics
            the fridge didn't answer), or None when the connection failed or no
            topic answered — a fridge that ACKs but publishes nothing is as
            offline as a dead TCP port.
        """
        cycle = self._cycle
        self._cycle += 1
        try:
            poll = self._poll(cycle)
        except DdmpError:
            return None
        if all(value is None for value in poll.values.values()):
            return None
        return poll

    def _poll(self, cycle: int) -> FridgePoll:
        """Run one connect→subscribe→collect→disconnect DDMP session."""
        values: dict[str, float | int | None] = {column: None for column in FRIDGE_COLUMNS}
        due_spans = [span for span, every in HISTORY_EVERY.items() if cycle % every == 0]
        history: list[dict[str, object]] = []
        with DdmpClient(self._host, self._port, deadline_s=POLL_DEADLINE_S) as client:
            client.ping()
            for column in TOPICS:
                if client.expired:
                    break
                client.request(TOPICS[column][0])
            for span in due_spans:
                if client.expired:
                    break
                client.request(HISTORY_PARAMS[span])
            # Drain briefly: publishes for the last subscribes may still be in flight.
            client.drain()
            for column, (param, kind) in TOPICS.items():
                frames = client.frames_for(param)
                if frames:
                    values[column] = decode_value(kind, frames[-1])
            poll_epoch = time.time()
            for span in due_spans:
                frames = client.frames_for(HISTORY_PARAMS[span])
                if not frames:
                    continue
                buckets, tail = decode_history(frames[-1])
                history.extend(flatten_history(span, buckets, tail, poll_epoch))
                if span == 'hour' and buckets:
                    # The in-progress ~10-min bucket is the live DC-draw signal.
                    values['dc_current_a'] = buckets[0]
        return FridgePoll(values, history)


class FakeFridge:
    """Synthetic fridge for desk-testing the MQTT → ``fridge_readings`` path.

    Drifts the continuous channels on a bounded random walk and holds the
    enum/bool channels at plausible fixed values, so the pipeline can be exercised
    with no fridge on the LAN (the real sink still publishes).
    """

    _WALKING: dict[str, float] = {
        'comp0_temp_c': 2.0,
        'comp1_temp_c': -14.0,
        'input_voltage_v': 26.6,
        'dc_current_a': 0.8,
    }
    _FIXED: dict[str, float | int] = {
        'comp0_set_c': 1.0,
        'comp1_set_c': -15.0,
        'comp0_door_open': 0,
        'comp1_door_open': 0,
        'comp0_power': 1,
        'comp1_power': 1,
        'cooler_power': 1,
        'power_source': 1,
        'battery_protection': 1,
        'temp_alert_cc': 0,
        'temp_alert_dcm': 0,
        'door_alert': 0,
        'voltage_alert': 0,
    }

    def __init__(self) -> None:
        self._values = dict(self._WALKING)

    def read(self) -> FridgePoll | None:
        """Return a full synthetic poll (always fresh), history included."""
        values: dict[str, float | int | None] = dict(self._FIXED)
        values.update(bounded_walk(self._values, self._WALKING, scale=0.02))
        dc = self._values['dc_current_a']
        now = time.time()
        history = [
            row
            for span in HISTORY_BUCKET_S
            for row in flatten_history(span, [round(dc + 0.1 * i, 1) for i in range(7)], 128, now)
        ]
        return FridgePoll(values, history)


def build_snapshot(poll: FridgePoll) -> dict[str, object]:
    """Build the publish payload: a timestamp, every column, and any history rows.

    Args:
        poll: The poll result (columns the fridge didn't answer may be absent).

    Returns:
        ``{'ts': <ms-UTC>, <column>: <value-or-None>, …}`` over all columns, plus
        ``history`` (the ``fridge_history`` UPSERT rows) when the poll carried any.
    """
    snapshot: dict[str, object] = {
        'ts': now_canonical(),
        **{col: poll.values.get(col) for col in FRIDGE_COLUMNS},
    }
    if poll.history:
        snapshot['history'] = poll.history
    return snapshot


def publish_loop(
    sensor: FridgeSource,
    client: object,
    reading_topic: str,
    status_topic: str,
    once: bool,
) -> None:
    """Poll→publish one snapshot per ``PUBLISH_INTERVAL_S``.

    Owns the retained ``status`` flag: a successful poll publishes the snapshot and
    flips ``online``; a failed one flips ``offline`` (the fridge's WiFi is off or out
    of range) rather than republishing stale values.

    Args:
        sensor: The fridge (or fake) source.
        client: The connected paho sink client (Pi broker).
        reading_topic: ``sensors/<node>/fridge`` to publish snapshots to.
        status_topic: ``sensors/<node>/fridge/status`` for the retained online flag.
        once: Poll and publish a single cycle, then return — for testing.
    """
    hb = Heartbeat()
    online = False
    client.publish(status_topic, 'offline', qos=1, retain=True)  # type: ignore[attr-defined]

    while True:
        poll = sensor.read()
        fresh = poll is not None
        if fresh != online:
            payload = 'online' if fresh else 'offline'
            client.publish(status_topic, payload, qos=1, retain=True)  # type: ignore[attr-defined]
            online = fresh

        if poll is not None:
            snapshot = json.dumps(build_snapshot(poll))
            client.publish(reading_topic, snapshot, qos=1, retain=True)  # type: ignore[attr-defined]
            hb.bump('published')
        else:
            hb.bump('unreachable')

        hb.maybe_emit(online=online)
        if once:
            return
        time.sleep(PUBLISH_INTERVAL_S)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list, or None to read from ``sys.argv``.

    Returns:
        The parsed argument namespace.
    """
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    add_publisher_args(
        parser,
        node_default='van',
        fake_help='Publish synthetic readings instead of polling a real fridge.',
        once_help='Poll and publish a single snapshot, then exit (for testing).',
    )
    return parser.parse_args(argv)


def main() -> int:
    """Connect the sink broker and run the poll→publish loop.

    Returns:
        Process exit code: 0 on graceful shutdown.
    """
    args = parse_args()
    sensor: FridgeSensor | FakeFridge = (
        FakeFridge() if args.fake else FridgeSensor(fridge_host(), fridge_port())
    )
    started = f'Fridge reader started (node={args.node}, fake={args.fake})'
    with publisher_session(args.node, SENSOR_TYPE, started_msg=started, announce_online=False) as (
        client,
        reading_topic,
        status_topic,
    ):
        try:
            publish_loop(sensor, client, reading_topic, status_topic, args.once)
        except KeyboardInterrupt:
            print('Fridge reader stopped', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
