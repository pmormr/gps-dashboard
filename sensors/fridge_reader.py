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

The stored column set is ``common.ddmp.TOPICS``. The DDMP error topics
(NTC/compressor/fan) are deliberately absent: the reference repo's params for them
are self-described mock-broker values, so subscribing would be guesswork. The four
alert topics are real.

Run::

    uv run sensors/fridge_reader.py --fake --node van   # synthetic fridge, real sink
    uv run sensors/fridge_reader.py --node van          # real fridge (CFX_HOST)
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from common.ddmp import (
    TOPICS,
    DdmpClient,
    DdmpError,
    decode_value,
    fridge_host,
    fridge_port,
)
from common.timefmt import now_canonical
from sensors.runner import (
    Heartbeat,
    Reading,
    SimpleSensor,
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

FRIDGE_COLUMNS: list[str] = list(TOPICS)


class FridgeSensor:
    """Polls one snapshot from the fridge's DDMP server per :meth:`read` call.

    Each poll is a fresh :class:`~common.ddmp.DdmpClient` session: PING, then a
    SUBSCRIBE per column, then a drain for in-flight publishes. Unanswered topics
    come back None — their columns store NULL for that cycle rather than failing
    the poll.
    """

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port

    def read(self) -> Reading | None:
        """Return one snapshot (column → value), or None if the fridge is unreachable.

        Returns:
            Every :data:`FRIDGE_COLUMNS` key (None for topics the fridge didn't
            answer), or None when the connection failed or no topic answered —
            a fridge that ACKs but publishes nothing is as offline as a dead TCP port.
        """
        try:
            values = self._poll()
        except DdmpError:
            return None
        if all(value is None for value in values.values()):
            return None
        return values

    def _poll(self) -> dict[str, float | int | None]:
        """Run one connect→subscribe→collect→disconnect DDMP session."""
        values: dict[str, float | int | None] = {column: None for column in FRIDGE_COLUMNS}
        with DdmpClient(self._host, self._port, deadline_s=POLL_DEADLINE_S) as client:
            client.ping()
            for column in FRIDGE_COLUMNS:
                if client.expired:
                    break
                client.request(TOPICS[column][0])
            # Drain briefly: publishes for the last subscribes may still be in flight.
            client.drain()
            for column, (param, kind) in TOPICS.items():
                frames = client.frames_for(param)
                if frames:
                    values[column] = decode_value(kind, frames[-1])
        return values


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

    def read(self) -> Reading | None:
        """Return a full synthetic snapshot (always fresh)."""
        snapshot: dict[str, float | int | None] = dict(self._FIXED)
        snapshot.update(bounded_walk(self._values, self._WALKING, scale=0.02))
        return snapshot


def build_snapshot(reading: Reading) -> dict[str, object]:
    """Build the publish payload: a timestamp plus every column from the reading.

    Args:
        reading: The polled values (columns the fridge didn't answer may be absent).

    Returns:
        ``{'ts': <ms-UTC>, <column>: <value-or-None>, …}`` over all columns.
    """
    return {'ts': now_canonical(), **{col: reading.get(col) for col in FRIDGE_COLUMNS}}


def publish_loop(
    sensor: SimpleSensor,
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
        reading = sensor.read()
        fresh = reading is not None
        if fresh != online:
            payload = 'online' if fresh else 'offline'
            client.publish(status_topic, payload, qos=1, retain=True)  # type: ignore[attr-defined]
            online = fresh

        if reading is not None:
            snapshot = json.dumps(build_snapshot(reading))
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
