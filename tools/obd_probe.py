"""OBD-II vehicle probe (Phase 0 of docs/obd-platform-plan.md).

Throwaway diagnostic for bringing up the OBD stream. It connects to an ELM327
reader (the owned BAFX over Bluetooth ``/dev/rfcomm0``, or the OBDLink EX over
USB ``/dev/ttyUSB0``), reports the negotiated protocol and the vehicle's
supported-PID set, reads any stored diagnostic trouble codes, then logs a chosen
set of live PIDs to JSONL.

Run it on the Pi, plugged into the van, **engine running** — that is the only
context that answers the Phase 0 questions: does the FCA Security Gateway pass
read-only polling, which PIDs the 3.6 Pentastar actually reports (notably whether
MAF ``0110`` is absent and fuel-rate ``015E`` is present), and the real per-cycle
throughput of the reader.

Examples:
    uv run tools/obd_probe.py                       # auto-scan serial ports
    uv run tools/obd_probe.py --port /dev/rfcomm0   # BAFX over Bluetooth
    uv run tools/obd_probe.py --log drive.jsonl --interval 0.5
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import time
from typing import TextIO

import obd

from common.cli import run_cli

# Probe column name -> OBD command. Names mirror the planned obd_readings
# columns so the JSONL log doubles as a preview of the eventual MQTT payload.
PROBE_COMMANDS: dict[str, obd.OBDCommand] = {
    'rpm': obd.commands.RPM,
    'speed_kph': obd.commands.SPEED,
    'coolant_c': obd.commands.COOLANT_TEMP,
    'intake_c': obd.commands.INTAKE_TEMP,
    'map_kpa': obd.commands.INTAKE_PRESSURE,
    'engine_load_pct': obd.commands.ENGINE_LOAD,
    'throttle_pct': obd.commands.THROTTLE_POS,
    'fuel_level_pct': obd.commands.FUEL_LEVEL,
    'fuel_rate_lph': obd.commands.FUEL_RATE,
    'voltage_v': obd.commands.CONTROL_MODULE_VOLTAGE,
    'run_time_s': obd.commands.RUN_TIME,
    'maf_g_s': obd.commands.MAF,
}

# Always polled regardless of the supported set: ATRV is an ELM AT-command, not a
# vehicle PID, so it answers even with the ignition off (a "is the dongle alive"
# signal) and gives a chassis-battery voltage when PID 0142 is unsupported.
ELM_VOLTAGE_COLUMN = 'elm_voltage_v'


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        The parsed argument namespace.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--port',
        default=None,
        help='Serial port (default: auto-scan, e.g. /dev/rfcomm0, /dev/ttyUSB0)',
    )
    parser.add_argument('--baud', type=int, default=None, help='Baud rate (default: auto-detect)')
    parser.add_argument(
        '--interval',
        type=float,
        default=0.5,
        help='Seconds to sleep between poll cycles (default: 0.5)',
    )
    parser.add_argument(
        '--duration',
        type=float,
        default=None,
        help='Stop after N seconds (default: run until Ctrl+C)',
    )
    parser.add_argument('--log', default=None, help='Append live readings to this JSONL file')
    parser.add_argument(
        '--fast',
        action='store_true',
        help='Enable python-OBD fast mode (default off; cheap clones are flakier with it)',
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable python-OBD DEBUG logging (connection troubleshooting)',
    )
    return parser.parse_args()


def numeric(response: obd.OBDResponse) -> float | None:
    """Extract a scalar magnitude from an OBD response.

    Args:
        response: The response returned by ``connection.query``.

    Returns:
        The response value as a float, or None if the response is null or not a
        scalar quantity.
    """
    if response is None or response.is_null():
        return None
    value = response.value
    magnitude = getattr(value, 'magnitude', value)
    try:
        return float(magnitude)
    except (TypeError, ValueError):
        return None


def connect(port: str | None, baud: int | None, fast: bool) -> obd.OBD:
    """Open a connection to the ELM327 reader.

    Args:
        port: Serial port path, or None to auto-scan.
        baud: Baud rate, or None to auto-detect.
        fast: Whether to enable python-OBD fast mode.

    Returns:
        The (possibly unconnected) OBD connection; the caller inspects status.
    """
    return obd.OBD(portstr=port, baudrate=baud, fast=fast)


def report_connection(connection: obd.OBD) -> None:
    """Print connection status, port, protocol, and dongle voltage."""
    status = connection.status()
    print('\n=== Connection ===')
    print(f'  port:     {connection.port_name()}')
    print(f'  status:   {status}')
    print(f'  protocol: {connection.protocol_name()} (id {connection.protocol_id()})')
    print(f'  {ELM_VOLTAGE_COLUMN}: {numeric(connection.query(obd.commands.ELM_VOLTAGE))} V')
    if status != obd.OBDStatus.CAR_CONNECTED:
        print("  WARNING: status is not CAR_CONNECTED — the ECU isn't answering.")
        print(
            '           Turn the ignition on (engine running); PID queries return null otherwise.'
        )


def report_supported(connection: obd.OBD) -> None:
    """Print every supported command plus a checklist of the probe's PIDs."""
    supported = connection.supported_commands
    print(f'\n=== Supported commands ({len(supported)}) ===')
    for cmd in sorted(supported, key=lambda c: c.command):
        print(f'  {cmd.command.decode():6s} {cmd.name:26s} {cmd.desc}')

    print('\n=== PIDs of interest (planned obd_readings columns) ===')
    for column, cmd in PROBE_COMMANDS.items():
        mark = 'yes' if cmd in supported else 'NO '
        print(f'  [{mark}] {column:16s} {cmd.name:26s} ({cmd.command.decode()})')


def report_dtcs(connection: obd.OBD) -> None:
    """Print the MIL/DTC-count summary and any stored trouble codes."""
    print('\n=== Diagnostic trouble codes ===')
    status = connection.query(obd.commands.STATUS)
    if not status.is_null():
        print(
            f'  MIL on: {getattr(status.value, "MIL", "?")}  '
            f'DTC count: {getattr(status.value, "DTC_count", "?")}'
        )
    dtcs = connection.query(obd.commands.GET_DTC)
    if dtcs.is_null() or not dtcs.value:
        print('  (none stored)')
        return
    for code, description in dtcs.value:
        print(f'  {code}  {description}')


def poll_columns(connection: obd.OBD) -> list[str]:
    """Return the probe columns supported by the vehicle, preserving order.

    Args:
        connection: The OBD connection (its supported set is consulted).

    Returns:
        The supported subset of ``PROBE_COMMANDS`` keys.
    """
    return [col for col, cmd in PROBE_COMMANDS.items() if cmd in connection.supported_commands]


def utc_now_ms() -> str:
    """Return the current UTC time as a fixed-width millisecond ISO string."""
    now = dt.datetime.now(dt.UTC)
    return now.strftime('%Y-%m-%dT%H:%M:%S.') + f'{now.microsecond // 1000:03d}Z'


def format_row(row: dict[str, object]) -> str:
    """Render a reading row as a compact ``key=value`` line, skipping nulls."""
    fields = ' '.join(f'{k}={v}' for k, v in row.items() if k != 'ts' and v is not None)
    return f'{row["ts"]}  {fields}'


def poll_loop(
    connection: obd.OBD,
    columns: list[str],
    interval: float,
    duration: float | None,
    log_file: TextIO | None,
) -> None:
    """Poll the supported columns on a fixed cadence until duration or Ctrl+C.

    Each cycle's wall-clock duration is printed so the real ELM327 throughput
    (which bounds the achievable cadence) is visible.

    Args:
        connection: The OBD connection.
        columns: Supported probe columns to query each cycle.
        interval: Seconds to sleep between cycles.
        duration: Stop after this many seconds, or None to run until interrupted.
        log_file: Open JSONL sink, or None to only print.
    """
    print(f'\n=== Polling {len(columns)} columns + {ELM_VOLTAGE_COLUMN} (Ctrl+C to stop) ===')
    start = time.monotonic()
    while True:
        cycle_start = time.monotonic()
        row: dict[str, object] = {'ts': utc_now_ms()}
        row[ELM_VOLTAGE_COLUMN] = numeric(connection.query(obd.commands.ELM_VOLTAGE))
        for col in columns:
            row[col] = numeric(connection.query(PROBE_COMMANDS[col]))
        row['cycle_s'] = round(time.monotonic() - cycle_start, 3)

        if log_file is not None:
            log_file.write(json.dumps(row) + '\n')
            log_file.flush()
        print(format_row(row))

        if duration is not None and (time.monotonic() - start) >= duration:
            break
        time.sleep(interval)


def main() -> None:
    """Connect, report capabilities, then log live readings."""
    args = parse_args()
    logging.getLogger('obd').setLevel(logging.DEBUG if args.verbose else logging.WARNING)

    connection = connect(args.port, args.baud, args.fast)
    try:
        report_connection(connection)
        if connection.status() == obd.OBDStatus.NOT_CONNECTED:
            print(
                '\nNo reader found. Check pairing/rfcomm bind (BAFX) or the USB path, then retry.'
            )
            sys.exit(1)

        report_supported(connection)
        report_dtcs(connection)

        columns = poll_columns(connection)
        if not columns:
            print(
                '\nNo probe PIDs supported (ignition off, or reads blocked?). Skipping live poll.'
            )
            return

        if args.log:
            with open(args.log, 'a', encoding='utf-8') as log_file:
                poll_loop(connection, columns, args.interval, args.duration, log_file)
        else:
            poll_loop(connection, columns, args.interval, args.duration, None)
    finally:
        connection.close()


if __name__ == '__main__':
    run_cli(main)
