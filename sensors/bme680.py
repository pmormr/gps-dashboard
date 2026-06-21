"""Pi-attached BME680 reader: I2C → MQTT publisher.

Reads a locally attached BME680 over I2C on a fixed interval and publishes JSON
readings to ``sensors/<node>/bme680``, registering a retained LWT on ``.../status``
so the broker flips the stream to ``offline`` if this process dies ungracefully.

Mirrors the GPS logger's daemon ethos: auto-reconnect (handled by paho's backoff),
a periodic heartbeat with a published/dropped breakdown, and graceful shutdown that
never raises on Ctrl+C.

A ``--fake`` mode synthesizes plausible readings so the MQTT → SQLite pipeline can
be exercised before the sensor is physically wired (Phase 1 de-risking).

Run::

    uv run sensors/bme680.py --fake --node cabin
    uv run sensors/bme680.py --node cabin            # real I2C hardware
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import UTC, datetime

from mqttbus import topics
from mqttbus.client import (
    KEEPALIVE_SECONDS,
    broker_host,
    broker_port,
    make_client,
)

SENSOR_TYPE = 'bme680'
READ_INTERVAL_SECONDS = 5
HEARTBEAT_SECONDS = 60


def default_node() -> str:
    """Return the node name from ``GPS_SENSOR_NODE`` (default ``cabin``)."""
    return os.environ.get('GPS_SENSOR_NODE', 'cabin')


def now_iso() -> str:
    """Return the current time as a whole-second UTC ISO-8601 string."""
    return datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


class FakeSensor:
    """Synthesize plausible BME680 readings for pipeline testing without hardware.

    Drives each metric on a small bounded random walk around a sane cabin baseline,
    so the published stream looks like a real sensor (slowly drifting values).
    """

    def __init__(self) -> None:
        self._temp_c = 22.0
        self._humidity_pct = 45.0
        self._pressure_hpa = 1013.0
        self._gas_ohms = 120000.0

    def read(self) -> dict[str, float] | None:
        """Return one synthetic reading, advancing the random walk.

        Returns:
            A dict of metric → value (never None; the fake sensor always reads).
        """
        self._temp_c += random.uniform(-0.2, 0.2)
        self._humidity_pct += random.uniform(-0.5, 0.5)
        self._pressure_hpa += random.uniform(-0.1, 0.1)
        self._gas_ohms += random.uniform(-2000, 2000)
        return {
            'temp_c': round(self._temp_c, 2),
            'humidity_pct': round(self._humidity_pct, 2),
            'pressure_hpa': round(self._pressure_hpa, 2),
            'gas_ohms': round(self._gas_ohms),
        }


class Bme680Sensor:
    """Real Pimoroni BME680 over I2C.

    The driver is imported lazily so a host without the I2C library (e.g. the dev
    machine running ``--fake``) does not need it installed to start.
    """

    def __init__(self) -> None:
        import bme680 as driver

        try:
            self._sensor = driver.BME680(driver.I2C_ADDR_PRIMARY)
        except (OSError, RuntimeError):
            self._sensor = driver.BME680(driver.I2C_ADDR_SECONDARY)
        s = self._sensor
        s.set_humidity_oversample(driver.OS_2X)
        s.set_pressure_oversample(driver.OS_4X)
        s.set_temperature_oversample(driver.OS_8X)
        s.set_filter(driver.FILTER_SIZE_3)
        s.set_gas_status(driver.ENABLE_GAS_MEAS)
        s.set_gas_heater_temperature(320)
        s.set_gas_heater_duration(150)
        s.select_gas_heater_profile(0)

    def read(self) -> dict[str, float] | None:
        """Return one reading from the sensor, or None if data isn't ready.

        ``gas_ohms`` is included only once the gas heater is stable; until then it
        is None (the reading is still useful for temp/humidity/pressure).

        Returns:
            A dict of metric → value, or None if the sensor had no fresh data.
        """
        s = self._sensor
        if not s.get_sensor_data():
            return None
        d = s.data
        return {
            'temp_c': round(d.temperature, 2),
            'humidity_pct': round(d.humidity, 2),
            'pressure_hpa': round(d.pressure, 2),
            'gas_ohms': round(d.gas_resistance) if d.heat_stable else None,
        }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list, or None to read from ``sys.argv``.

    Returns:
        Parsed arguments with ``node``, ``fake``, ``interval``, and ``once``.
    """
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument(
        '--node',
        default=default_node(),
        help='Node name / location (topic <node> segment). Default $GPS_SENSOR_NODE or "cabin".',
    )
    parser.add_argument(
        '--fake',
        action='store_true',
        help='Publish synthetic readings instead of reading I2C hardware.',
    )
    parser.add_argument(
        '--interval',
        type=float,
        default=READ_INTERVAL_SECONDS,
        help=f'Seconds between readings (default {READ_INTERVAL_SECONDS}).',
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Publish a single reading and exit (for testing).',
    )
    return parser.parse_args(argv)


def main() -> int:
    """Run the reader loop, publishing readings until interrupted.

    Returns:
        Process exit code: 0 on graceful shutdown.
    """
    args = parse_args()
    node = args.node
    reading_topic = topics.reading_topic(node, SENSOR_TYPE)
    status_topic = topics.status_topic(node, SENSOR_TYPE)
    sensor = FakeSensor() if args.fake else Bme680Sensor()

    def on_connect(client, userdata, flags, reason_code, properties) -> None:
        client.publish(status_topic, 'online', qos=1, retain=True)
        print(f'reader connected ({reason_code}); status online on {status_topic}', flush=True)

    client = make_client(
        f'gps-sensor-{node}-{SENSOR_TYPE}',
        lwt_topic=status_topic,
        lwt_payload='offline',
    )
    client.on_connect = on_connect
    client.connect(broker_host(), broker_port(), keepalive=KEEPALIVE_SECONDS)
    client.loop_start()

    print(
        f'BME680 reader started (node={node}, fake={args.fake}, topic={reading_topic})', flush=True
    )
    written = 0
    dropped = 0
    last_heartbeat = time.monotonic()
    try:
        while True:
            reading = sensor.read()
            if reading is None:
                dropped += 1
            else:
                payload = {'ts': now_iso(), **reading}
                client.publish(reading_topic, json.dumps(payload), qos=1, retain=True)
                written += 1
            if args.once:
                time.sleep(0.2)
                break
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_SECONDS:
                print(f'heartbeat: published={written} dropped={dropped} node={node}', flush=True)
                written = 0
                dropped = 0
                last_heartbeat = now
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('BME680 reader stopped', flush=True)
    finally:
        client.publish(status_topic, 'offline', qos=1, retain=True)
        time.sleep(0.2)
        client.loop_stop()
        client.disconnect()
    return 0


if __name__ == '__main__':
    sys.exit(main())
