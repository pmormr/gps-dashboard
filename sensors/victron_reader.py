"""Pi-side Victron reader: Venus OS GX MQTT → MQTT publisher (house power as a sensor).

Bridges the van's Victron power system into the sensor platform. A Venus OS GX device
(the Cerbo-class controller at ``$VICTRON_MQTT_HOST``) publishes its whole system over
its *own* MQTT broker under ``N/<portal-id>/<service>/<instance>/<path>``. This reader
subscribes there, keeps a latest-value cache, and republishes a compact snapshot to
``sensors/<node>/victron`` on the *Pi's* broker every ``PUBLISH_INTERVAL_S``, where
``mqtt-ingest`` writes it into ``victron_readings`` (one row per snapshot) — see
``mqttbus/ingest.py`` and ``api/sensor_schema.py``.

Two brokers, not one (unlike the OBD reader): a **source** client on the GX broker
(authenticated — the password is the only secret, supplied via the environment) and a
**sink** client on the Pi broker (anonymous, the usual LWT-on-``status`` pattern).

Venus OS quirks handled here:

* **Keepalive.** After its connect-time full publish, Venus stops sending unless it
  receives a keepalive within ~60 s. We learn the portal-id from the first message and
  publish an empty ``R/<portal-id>/keepalive`` on connect and on every snapshot tick.
* **Instance numbers drift.** A device's instance (``battery/278``, ``vebus/276``) is
  not stable across reconfigurations, so the topic→column map wildcards the instance
  segment; the stable ``system/0`` aggregates cover most columns.
* **Source staleness.** If the GX goes silent while this process stays up, we stop
  emitting and flip the stream offline rather than republish a frozen cache — the GPS
  logger's staleness-watchdog ethos. Not engine-gated: the GX runs 24/7.

Run::

    uv run sensors/victron_reader.py --fake --node house   # synthetic source, real sink
    uv run sensors/victron_reader.py --node house          # real GX (needs VICTRON_MQTT_*)
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time

import paho.mqtt.client as mqtt

from common.timefmt import now_canonical
from mqttbus.client import KEEPALIVE_SECONDS, make_client
from sensors.runner import (
    Heartbeat,
    add_publisher_args,
    bounded_walk,
    publish_reading,
    publish_status,
    publisher_session,
)

SENSOR_TYPE = 'victron'

# Snapshot cadence (matches the BME680 node). Power data is slow-moving, and it must
# stay under Venus's ~60 s keepalive window since each tick re-sends the keepalive.
PUBLISH_INTERVAL_S = 30.0
# Treat the source as dead if no GX message has arrived within this window. Larger
# than the publish cadence so a single missed change doesn't flap the stream offline.
SOURCE_STALE_S = 90.0

# The GX subtrees we care about, with the portal-id segment wildcarded (`+`) so the
# subscription needs no portal-id up front — far less firehose than `N/#`.
SOURCE_SUBSCRIPTIONS = ('N/+/system/#', 'N/+/solarcharger/#', 'N/+/vebus/#')

#: Venus relative topic (instance wildcarded to ``*``) → ``victron_readings`` column.
#: ``system/0`` aggregates are stable; device-level paths use ``*`` because instance
#: numbers are not stable across GX reconfigurations.
TOPIC_MAP: dict[str, str] = {
    'system/*/Dc/Battery/Soc': 'battery_soc',
    'system/*/Dc/Battery/Voltage': 'battery_voltage',
    'system/*/Dc/Battery/Current': 'battery_current',
    'system/*/Dc/Battery/Power': 'battery_power',
    'system/*/Dc/Battery/Temperature': 'battery_temp_c',
    'system/*/Dc/Battery/ConsumedAmphours': 'consumed_ah',
    'system/*/Dc/Battery/TimeToGo': 'time_to_go_s',
    'system/*/Dc/Battery/State': 'battery_state',
    'system/*/Dc/Pv/Power': 'pv_power',
    'system/*/Dc/System/Power': 'dc_system_power',
    'system/*/Ac/ActiveIn/L1/Power': 'ac_in_power',
    'system/*/Ac/ActiveIn/L1/Current': 'ac_in_current',
    'system/*/Ac/ActiveIn/Source': 'ac_in_source',
    'system/*/Ac/Consumption/L1/Power': 'ac_consumption_power',
    'solarcharger/*/Pv/V': 'pv_voltage',
    'solarcharger/*/History/Daily/0/Yield': 'pv_yield_today_kwh',
    'solarcharger/*/State': 'solar_state',
    'vebus/*/State': 'vebus_state',
    'vebus/*/Mode': 'vebus_mode',
}

#: Snapshot column order (also the chart display order); pinned to the shared
#: ``victron`` schema by a test so the two can't drift.
VICTRON_COLUMNS: list[str] = [
    'battery_soc',
    'battery_voltage',
    'battery_current',
    'battery_power',
    'battery_temp_c',
    'consumed_ah',
    'time_to_go_s',
    'battery_state',
    'pv_power',
    'pv_voltage',
    'pv_yield_today_kwh',
    'solar_state',
    'dc_system_power',
    'ac_in_power',
    'ac_in_current',
    'ac_in_source',
    'ac_consumption_power',
    'vebus_state',
    'vebus_mode',
]

Number = float | int


def victron_host() -> str:
    """Return the GX broker host from ``VICTRON_MQTT_HOST`` (default the GX IP)."""
    return os.environ.get('VICTRON_MQTT_HOST', '192.168.42.234')


def victron_port() -> int:
    """Return the GX broker port from ``VICTRON_MQTT_PORT`` (default 1883; 8883 = TLS)."""
    return int(os.environ.get('VICTRON_MQTT_PORT', '1883'))


def victron_password() -> str | None:
    """Return the GX MQTT password from ``VICTRON_MQTT_PASSWORD`` (None if unset)."""
    return os.environ.get('VICTRON_MQTT_PASSWORD') or None


def column_for_topic(rel: str) -> str | None:
    """Map a Venus relative topic (the part after ``N/<portal>/``) to a column.

    The instance segment (index 1, e.g. the ``278`` in ``battery/278/…``) is
    wildcarded to ``*`` before lookup, since Venus device instances are not stable
    across reconfigurations; ``system`` aggregates always sit at instance 0.

    Args:
        rel: The topic with the ``N/<portal>/`` prefix already stripped, e.g.
            ``system/0/Dc/Battery/Soc``.

    Returns:
        The ``victron_readings`` column, or None if the topic is not one we store.
    """
    parts = rel.split('/')
    if len(parts) < 2:
        return None
    parts[1] = '*'
    return TOPIC_MAP.get('/'.join(parts))


def parse_value(raw: bytes) -> Number | None:
    """Extract a numeric value from a Venus ``{"value": …}`` MQTT payload.

    Args:
        raw: The raw MQTT payload bytes.

    Returns:
        The numeric value, or None if the payload is null, non-numeric, or unparseable
        (every mapped column is numeric, so non-numbers store as NULL).
    """
    try:
        payload = json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError):
        return None
    value = payload.get('value') if isinstance(payload, dict) else payload
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return value
    return None


def build_snapshot(cache: dict[str, Number | None]) -> dict[str, object]:
    """Build the publish payload: a timestamp plus every column from the cache.

    Args:
        cache: Latest value per column (None for columns not yet seen).

    Returns:
        ``{'ts': <ms-UTC>, <column>: <value-or-None>, …}`` over all columns.
    """
    return {'ts': now_canonical(), **{col: cache.get(col) for col in VICTRON_COLUMNS}}


class VictronSource:
    """Owns the GX-broker client, the latest-value cache, and the keepalive.

    Subscribes the GX subtrees we map, learns the portal-id from the first message
    (needed to address the keepalive), and tracks message liveness so the publish loop
    can flip the stream offline when the GX goes silent.
    """

    def __init__(self, host: str, port: int, password: str | None) -> None:
        self._host = host
        self._port = port
        self._cache: dict[str, Number | None] = {col: None for col in VICTRON_COLUMNS}
        self._portal: str | None = None
        self._last_msg = 0.0
        client = make_client('gps-victron-source', clean_session=True)
        if password is not None:
            client.username_pw_set('', password)
        if port == 8883:
            client.tls_set(cert_reqs=ssl.CERT_NONE)
            client.tls_insecure_set(True)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        self._client = client

    @property
    def portal(self) -> str | None:
        """The discovered GX portal-id, or None until the first message arrives."""
        return self._portal

    def _on_connect(
        self, client: mqtt.Client, userdata: object, flags: object, reason_code: object, *_: object
    ) -> None:
        for sub in SOURCE_SUBSCRIPTIONS:
            client.subscribe(sub, qos=0)
        print(f'victron source connected ({reason_code})', flush=True)
        if self._portal is not None:
            self._send_keepalive()

    def _on_message(self, client: mqtt.Client, userdata: object, msg: mqtt.MQTTMessage) -> None:
        parts = msg.topic.split('/')
        if len(parts) < 3 or parts[0] != 'N':
            return
        self._last_msg = time.monotonic()
        if self._portal is None:
            self._portal = parts[1]
            self._send_keepalive()
        column = column_for_topic('/'.join(parts[2:]))
        if column is not None:
            self._cache[column] = parse_value(msg.payload)

    def _send_keepalive(self) -> None:
        """Publish an empty keepalive so the GX keeps sending (no-op before portal-id)."""
        if self._portal is not None:
            self._client.publish(f'R/{self._portal}/keepalive', '', qos=0)

    def start(self) -> None:
        """Connect to the GX broker and start the network loop."""
        self._client.connect(self._host, self._port, keepalive=KEEPALIVE_SECONDS)
        self._client.loop_start()

    def tick(self) -> None:
        """Re-send the keepalive; call once per snapshot (< 60 s apart)."""
        self._send_keepalive()

    def snapshot(self) -> dict[str, Number | None]:
        """Return a copy of the latest-value cache."""
        return dict(self._cache)

    def fresh(self) -> bool:
        """Return whether a GX message has arrived within ``SOURCE_STALE_S``."""
        return self._last_msg > 0 and (time.monotonic() - self._last_msg) <= SOURCE_STALE_S

    def stop(self) -> None:
        """Stop the network loop and disconnect from the GX broker."""
        self._client.loop_stop()
        self._client.disconnect()


class FakeSource:
    """Synthetic source for desk-testing the MQTT → ``victron_readings`` path.

    Always reports fresh and drifts a plausible snapshot on a bounded random walk, so
    the pipeline can be exercised with no GX on the LAN (the real sink still publishes).
    """

    _BASELINES: dict[str, float] = {
        'battery_soc': 57.0,
        'battery_voltage': 27.2,
        'battery_current': 33.6,
        'battery_power': 914.0,
        'battery_temp_c': 24.0,
        'consumed_ah': -46.9,
        'time_to_go_s': 0.0,
        'battery_state': 1.0,
        'pv_power': 13.7,
        'pv_voltage': 54.9,
        'pv_yield_today_kwh': 0.75,
        'solar_state': 3.0,
        'dc_system_power': 109.0,
        'ac_in_power': 1078.0,
        'ac_in_current': 9.3,
        'ac_in_source': 1.0,
        'ac_consumption_power': 0.0,
        'vebus_state': 3.0,
        'vebus_mode': 3.0,
    }

    def __init__(self) -> None:
        self._values = dict(self._BASELINES)

    @property
    def portal(self) -> str:
        """A stand-in portal-id for the heartbeat line."""
        return 'fake'

    def start(self) -> None:
        """No-op; the fake source holds no connection."""

    def tick(self) -> None:
        """No-op; the fake source needs no keepalive."""

    def snapshot(self) -> dict[str, Number | None]:
        """Return a drifted plausible value for every column."""
        out: dict[str, Number | None] = {}
        for column, value in bounded_walk(self._values, self._BASELINES, scale=0.02).items():
            out[column] = value
        return out

    def fresh(self) -> bool:
        """The fake source is always fresh."""
        return True

    def stop(self) -> None:
        """No-op; the fake source holds no resources."""


def publish_loop(
    source: VictronSource | FakeSource,
    client: mqtt.Client,
    reading_topic: str,
    status_topic: str,
    once: bool,
) -> None:
    """Emit one ``sensors/<node>/victron`` snapshot per ``PUBLISH_INTERVAL_S``.

    Re-sends the GX keepalive each tick, publishes a full snapshot while the source is
    fresh, and flips the retained ``status`` between ``online``/``offline`` on source
    freshness transitions (rather than republish a frozen cache when the GX is silent).

    Args:
        source: The GX (or fake) source; owns the value cache and liveness.
        client: The connected paho sink client (Pi broker).
        reading_topic: ``sensors/<node>/victron`` to publish snapshots to.
        status_topic: ``sensors/<node>/victron/status`` for the retained online flag.
        once: Publish a single snapshot (while fresh) and return — for testing.
    """
    hb = Heartbeat()
    online = False
    publish_status(client, status_topic, 'offline')

    while True:
        source.tick()
        fresh = source.fresh()
        if fresh != online:
            publish_status(client, status_topic, 'online' if fresh else 'offline')
            online = fresh

        if fresh:
            snapshot = json.dumps(build_snapshot(source.snapshot()))
            publish_reading(client, reading_topic, snapshot)
            hb.bump('published')
        else:
            hb.bump('stale')

        hb.maybe_emit(online=online, portal=source.portal)
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
        node_default='house',
        fake_help='Publish synthetic readings instead of reading a real GX device.',
        once_help='Publish a single snapshot (while fresh) and exit (for testing).',
    )
    return parser.parse_args(argv)


def main() -> int:
    """Connect both brokers and run the publish loop.

    The sink lifecycle is :func:`sensors.runner.publisher_session` with
    ``announce_online=False`` — unlike the other readers, the publish loop owns the
    retained ``status`` flag, flipping it on GX-source freshness transitions rather
    than declaring ``online`` the moment the sink connects.

    Returns:
        Process exit code: 0 on graceful shutdown.
    """
    args = parse_args()
    source: VictronSource | FakeSource = (
        FakeSource()
        if args.fake
        else VictronSource(victron_host(), victron_port(), victron_password())
    )
    started = f'Victron reader started (node={args.node}, fake={args.fake})'
    with publisher_session(args.node, SENSOR_TYPE, started_msg=started, announce_online=False) as (
        client,
        reading_topic,
        status_topic,
    ):
        source.start()
        try:
            publish_loop(source, client, reading_topic, status_topic, args.once)
        except KeyboardInterrupt:
            print('Victron reader stopped', flush=True)
        finally:
            source.stop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
