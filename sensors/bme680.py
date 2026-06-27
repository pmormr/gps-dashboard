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
import random
import sys

from sensors.runner import SimpleSensor, add_publisher_args, run_simple_publisher

SENSOR_TYPE = 'bme680'
READ_INTERVAL_SECONDS = 5.0


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
    add_publisher_args(
        parser,
        node_default='cabin',
        fake_help='Publish synthetic readings instead of reading I2C hardware.',
        once_help='Publish a single reading and exit (for testing).',
    )
    parser.add_argument(
        '--interval',
        type=float,
        default=READ_INTERVAL_SECONDS,
        help=f'Seconds between readings (default {READ_INTERVAL_SECONDS}).',
    )
    return parser.parse_args(argv)


def main() -> int:
    """Run the reader loop, publishing readings until interrupted.

    Returns:
        Process exit code: 0 on graceful shutdown.
    """
    args = parse_args()
    sensor: SimpleSensor = FakeSensor() if args.fake else Bme680Sensor()
    run_simple_publisher(
        sensor,
        node=args.node,
        sensor_type=SENSOR_TYPE,
        interval=args.interval,
        once=args.once,
        started_msg=f'BME680 reader started (node={args.node}, fake={args.fake})',
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
