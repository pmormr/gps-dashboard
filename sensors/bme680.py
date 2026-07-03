"""Fake BME680 publisher: the MQTT → SQLite pipeline test harness.

Synthesizes plausible cabin readings on a fixed interval and publishes JSON to
``sensors/<node>/bme680``, registering a retained LWT on ``.../status`` so the
broker flips the stream to ``offline`` if this process dies ungracefully.

The *live* BME680 is the ESPHome ESP32 node (``firmware/cabin-bme680.yaml``);
any future BME680-class sensor takes that same ESP-side path. This script exists
purely to exercise the broker → ingest → DB pipeline without hardware.

Run::

    uv run sensors/bme680.py --node cabin
"""

import argparse
import random
import sys

from sensors.runner import add_publisher_args, run_simple_publisher

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
        fake_help='No-op (this harness always synthesizes); kept for CLI uniformity.',
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
    """Run the harness loop, publishing synthetic readings until interrupted.

    Returns:
        Process exit code: 0 on graceful shutdown.
    """
    args = parse_args()
    run_simple_publisher(
        FakeSensor(),
        node=args.node,
        sensor_type=SENSOR_TYPE,
        interval=args.interval,
        once=args.once,
        started_msg=f'BME680 fake publisher started (node={args.node})',
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
