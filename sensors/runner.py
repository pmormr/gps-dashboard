"""Shared framework for the Pi-side sensor readers (the publisher side of the bus).

Every reader (``bme680``, ``obd_reader``, ``victron_reader``) is the same daemon
around a different source: connect a sink client to the Pi broker, publish a JSON
snapshot to ``sensors/<node>/<type>``, keep a retained ``.../status`` flag the LWT
flips to ``offline`` on an ungraceful exit, and emit a periodic heartbeat. This
module owns that scaffolding so each reader is just its source plus its column
table:

* :func:`publisher_session` — the connect/teardown lifecycle as a context manager.
* :func:`run_simple_publisher` — a full read→publish→heartbeat loop for the simple
  fixed-interval readers (BME680 and any future plain sensor).
* :func:`run_fleet_publisher` — the multi-stream variant for one reader feeding
  several nodes (the Dahua fleet): one process, one session **per stream**, since
  an MQTT LWT is per-connection — a shared client could flip only one stream's
  status to ``offline`` on an ungraceful death, leaving the rest stale-online.
* :class:`Heartbeat` — an interval-gated counter line (a stall names its own cause).
* :func:`bounded_step` / :func:`bounded_walk` — the bounded random walk the
  ``--fake`` sources drift on.
* :func:`default_node` / :func:`add_publisher_args` — the shared ``--node/--fake/
  --once`` CLI surface.

It builds on the low-level :mod:`mqttbus.client` primitives; ``mqttbus`` itself
stays pure transport and has no dependency back on this framework.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import time
from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from typing import Protocol

import paho.mqtt.client as mqtt

from common.timefmt import now_canonical
from mqttbus import topics
from mqttbus.client import KEEPALIVE_SECONDS, broker_host, broker_port, make_client

HEARTBEAT_SECONDS = 60.0

#: A reading snapshot's value columns (the ``ts`` key is added at publish time).
#: A ``Mapping`` (not ``dict``) so a sensor returning a narrower ``dict[str, float]``
#: still satisfies the :class:`SimpleSensor` protocol despite ``dict`` invariance.
Reading = Mapping[str, float | int | None]


class SimpleSensor(Protocol):
    """A fixed-interval source for :func:`run_simple_publisher`."""

    def read(self) -> Reading | None:
        """Return one reading (metric→value), or None if no fresh data."""


class FleetSensor(Protocol):
    """A fixed-interval multi-node source for :func:`run_fleet_publisher`."""

    def read(self) -> Mapping[str, Reading | None]:
        """Return node → reading for one poll cycle (None = drop that node's cycle)."""


def default_node(default: str) -> str:
    """Return the node name from ``GPS_SENSOR_NODE``, or ``default`` if unset.

    Args:
        default: The reader's fallback node name (e.g. ``cabin``/``van``/``house``).

    Returns:
        The configured node name.
    """
    return os.environ.get('GPS_SENSOR_NODE', default)


def add_publisher_args(
    parser: argparse.ArgumentParser,
    *,
    node_default: str,
    fake_help: str,
    once_help: str,
) -> None:
    """Add the ``--node/--fake/--once`` arguments every reader shares.

    Args:
        parser: The reader's argument parser; reader-specific args are added by the
            caller around this.
        node_default: The reader's default node name (also shown in ``--node`` help).
        fake_help: Help text for ``--fake`` (the source the reader fakes differs).
        once_help: Help text for ``--once`` (when a single emit happens differs).
    """
    parser.add_argument(
        '--node',
        default=default_node(node_default),
        help=f'Node name (topic <node> segment). Default $GPS_SENSOR_NODE or "{node_default}".',
    )
    parser.add_argument('--fake', action='store_true', help=fake_help)
    parser.add_argument('--once', action='store_true', help=once_help)


class Heartbeat:
    """Interval-gated heartbeat line with named counters that reset on each emit.

    Accumulating counts via :meth:`bump` and point-in-time context via the
    :meth:`maybe_emit` kwargs keeps the per-window stats bookkeeping out of every
    reader. The emitted line is ``heartbeat: <context…> <counters…>``; field order
    is for human log reading only, nothing parses it.
    """

    def __init__(self, *, interval_s: float = HEARTBEAT_SECONDS, prefix: str = 'heartbeat') -> None:
        self._interval = interval_s
        self._prefix = prefix
        self._last = time.monotonic()
        self._counts: dict[str, int] = {}

    def bump(self, name: str, n: int = 1) -> None:
        """Add ``n`` to the named counter for the current window."""
        self._counts[name] = self._counts.get(name, 0) + n

    def maybe_emit(self, **context: object) -> bool:
        """Print and reset the window if the interval has elapsed.

        Args:
            **context: Point-in-time fields (state, voltage, …) rendered before the
                accumulated counters.

        Returns:
            True if a heartbeat was emitted (window reset), False if not yet due.
        """
        now = time.monotonic()
        if now - self._last < self._interval:
            return False
        fields = {**context, **self._counts}
        line = ' '.join(f'{key}={value}' for key, value in fields.items())
        print(f'{self._prefix}: {line}', flush=True)
        self._counts.clear()
        self._last = now
        return True


def bounded_step(value: float, baseline: float, *, scale: float) -> float:
    """Advance ``value`` one bounded random step sized relative to ``baseline``.

    The step is ``±scale·(|baseline|+1)`` so each column wanders at a plausible rate
    regardless of magnitude. There is no hard clamp — over a test run the walk stays
    near the baseline because the step is small and unbiased.

    Args:
        value: The current value.
        baseline: The column's nominal value (sets the step magnitude).
        scale: Step size as a fraction of ``|baseline|+1``.

    Returns:
        The stepped value (unrounded).
    """
    return value + random.uniform(-scale, scale) * (abs(baseline) + 1.0)


def bounded_walk(
    state: MutableMapping[str, float],
    baselines: Mapping[str, float],
    *,
    scale: float,
    round_to: int = 2,
) -> dict[str, float]:
    """Step every column in ``state`` once and return a rounded snapshot.

    Args:
        state: Mutable per-column running values, advanced in place.
        baselines: Each column's nominal value (sets per-column step size).
        scale: Step size as a fraction of ``|baseline|+1``.
        round_to: Decimal places to round the returned snapshot to.

    Returns:
        A fresh dict of column→rounded value for this tick.
    """
    snapshot: dict[str, float] = {}
    for column, baseline in baselines.items():
        state[column] = bounded_step(state[column], baseline, scale=scale)
        snapshot[column] = round(state[column], round_to)
    return snapshot


@contextmanager
def publisher_session(
    node: str,
    sensor_type: str,
    *,
    started_msg: str | None = None,
    announce_online: bool = True,
) -> Iterator[tuple[mqtt.Client, str, str]]:
    """Connect a sink publisher for one stream; yield ``(client, reading, status)``.

    On enter: build the reading/status topics, make a client with a retained
    ``offline`` LWT, register an ``on_connect`` that publishes ``online`` (unless
    ``announce_online`` is False — the Victron and OBD readers own their status from
    the loop instead: source freshness and physical link state respectively),
    then connect and start the network loop. On exit (including Ctrl+C or an error):
    publish a retained ``offline``, briefly drain, and stop/disconnect.

    Args:
        node: The node (location) topic segment.
        sensor_type: The sensor-type topic segment (e.g. ``bme680``).
        started_msg: A line to print once connected, or None.
        announce_online: Whether ``on_connect`` publishes the retained ``online``.

    Yields:
        ``(client, reading_topic, status_topic)`` — the connected client and the two
        topics for this stream.
    """
    reading_topic = topics.reading_topic(node, sensor_type)
    status_topic = topics.status_topic(node, sensor_type)

    def on_connect(
        client: mqtt.Client, userdata: object, flags: object, reason_code: object, *_: object
    ) -> None:
        if announce_online:
            client.publish(status_topic, 'online', qos=1, retain=True)
        print(f'{sensor_type} reader connected ({reason_code})', flush=True)

    client = make_client(
        f'gps-sensor-{node}-{sensor_type}',
        lwt_topic=status_topic,
        lwt_payload='offline',
    )
    client.on_connect = on_connect
    client.connect(broker_host(), broker_port(), keepalive=KEEPALIVE_SECONDS)
    client.loop_start()
    if started_msg:
        print(started_msg, flush=True)
    try:
        yield client, reading_topic, status_topic
    finally:
        client.publish(status_topic, 'offline', qos=1, retain=True)
        time.sleep(0.2)
        client.loop_stop()
        client.disconnect()


def run_fleet_publisher(
    sensor: FleetSensor,
    *,
    streams: Mapping[str, str],
    interval: float,
    once: bool,
    started_msg: str | None = None,
) -> None:
    """Read→publish a whole fleet on a fixed interval until interrupted.

    The multi-stream sibling of :func:`run_simple_publisher`: one process holds a
    :func:`publisher_session` per stream (each with its own client and LWT — see
    the module docstring for why they can't share one), reads the fleet in one
    call, and publishes each node's snapshot to its own topic. A node whose
    reading is None this cycle is a drop for that node only.

    Args:
        sensor: The fleet source; ``read`` returns node → reading (or None).
        streams: Node → sensor type, one entry per published stream. Every key
            ``sensor.read`` returns must be present here.
        interval: Seconds between fleet reads.
        once: Publish a single fleet reading and return (for testing).
        started_msg: A line to print once connected, or None.
    """
    hb = Heartbeat()
    with contextlib.ExitStack() as stack:
        sessions: dict[str, tuple[mqtt.Client, str]] = {}
        for node, sensor_type in streams.items():
            client, reading_topic, _status = stack.enter_context(
                publisher_session(node, sensor_type)
            )
            sessions[node] = (client, reading_topic)
        if started_msg:
            print(started_msg, flush=True)
        try:
            while True:
                for node, reading in sensor.read().items():
                    if reading is None:
                        hb.bump('dropped')
                        continue
                    client, reading_topic = sessions[node]
                    payload = {'ts': now_canonical(), **reading}
                    client.publish(reading_topic, json.dumps(payload), qos=1, retain=True)
                    hb.bump('published')
                if once:
                    time.sleep(0.2)
                    return
                hb.maybe_emit(streams=len(sessions))
                time.sleep(interval)
        except KeyboardInterrupt:
            print('fleet reader stopped', flush=True)


def run_simple_publisher(
    sensor: SimpleSensor,
    *,
    node: str,
    sensor_type: str,
    interval: float,
    once: bool,
    started_msg: str | None = None,
) -> None:
    """Read→publish on a fixed interval until interrupted (the simple-reader loop).

    The full loop for a plain sensor: connect via :func:`publisher_session`, publish
    a ``{'ts': …, **reading}`` snapshot each interval (counting drops when the source
    has no fresh data), and emit a heartbeat. ``--once`` publishes a single reading
    and returns (briefly draining so the publish flushes).

    Args:
        sensor: The source; its ``read`` returns a reading or None.
        node: The node topic segment.
        sensor_type: The sensor-type topic segment.
        interval: Seconds between readings.
        once: Publish a single reading and return (for testing).
        started_msg: A line to print once connected, or None.
    """
    hb = Heartbeat()
    with publisher_session(node, sensor_type, started_msg=started_msg) as (
        client,
        reading_topic,
        _status,
    ):
        try:
            while True:
                reading = sensor.read()
                if reading is None:
                    hb.bump('dropped')
                else:
                    payload = {'ts': now_canonical(), **reading}
                    client.publish(reading_topic, json.dumps(payload), qos=1, retain=True)
                    hb.bump('published')
                if once:
                    time.sleep(0.2)
                    return
                hb.maybe_emit(node=node)
                time.sleep(interval)
        except KeyboardInterrupt:
            print(f'{sensor_type} reader stopped', flush=True)
