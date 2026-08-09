"""Pi-side OBD-II reader: ELM327 serial → MQTT publisher (the van as a sensor).

Reads the van's ECUs over an ELM327 adapter (the OBDLink EX on ``/dev/obdlink``,
reached through the 12+8 Security-Gateway bypass harness) and publishes a full
current-values snapshot to ``sensors/van/obd``, registering a retained LWT on
``.../status`` so the broker flips the stream to ``offline`` if this process dies.
The ingest subscriber writes each snapshot into ``obd_readings`` (one row per
cycle, complete) — see ``mqttbus/ingest.py`` and ``api/sensor_schema.py``.

Beyond ``online``/``offline``, the retained status also carries the **physical
link state** so an unplugged cable is distinguishable from a parked van: each
parked wake classifies the link (``no_adapter`` — serial/ELM unreachable, the USB
side is unplugged; ``no_car`` — the ELM answers but reads < 6 V battery voltage,
so the adapter is out of the OBD socket; ``online`` — socket powered) and
publishes transitions. Like the Victron reader, the loop owns the status topic
(``announce_online=False``) so a broker reconnect can't stomp the retained state.

Two design points define this reader:

* **Single serial owner, no queue.** The ELM327 is a blocking, half-duplex,
  request-response device: one query at a time, the call paces itself. This reader
  is the sole owner of the serial line; nothing else queries it, so there is no bus
  to overwhelm and no queue to manage.
* **Engine-gated polling.** The dongle hangs off the always-on OBD pin and would
  drain the *starter* battery if it polled forever. When the engine is off the
  reader **closes the connection** (lets the CAN bus sleep) and only wakes
  periodically to check the adapter voltage; alternator voltage (~14 V vs. ~12.2 V
  parked) is the engine-running signal. This also bounds logging to drive-time.

Polling is driven by a **per-PID rate table** (``PID_SPECS``). Today every PID
shares one baseline period, so each cycle is a full fresh snapshot; the table is the
seed a future on-demand viewer would modulate (a live gauge raising RPM's rate while
it is open — deferred). ``fuel_rate_lph`` has no PID on the speed-density Pentastar —
it is derived at read time (``common/obd.py``), so the reader leaves that column
unset (NULL).

The module is ``obd_reader`` (not ``obd``) so a script-form run does not shadow the
``obd`` library: ``python sensors/obd.py`` would put ``sensors/`` first on the path
and ``import obd`` would find this file instead of the package.

Run::

    uv run sensors/obd_reader.py --fake --node van   # synthetic, no hardware/broker car
    uv run sensors/obd_reader.py --port /dev/obdlink  # real adapter (engine running)
"""

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Literal, Protocol

import obd

from common.timefmt import now_canonical
from sensors.runner import (
    Heartbeat,
    Publisher,
    add_publisher_args,
    bounded_step,
    publish_reading,
    publish_status,
    publisher_session,
)

SENSOR_TYPE = 'obd'

#: Physical-link classification published (retained) on the stream's status topic.
#: ``offline`` (process dead, via LWT) completes the vocabulary but is never a
#: probe result. python-OBD's ELM327 init already measures battery voltage
#: (``AT RV``): < 6 V leaves it at ``ELM_CONNECTED`` — the out-of-socket signature,
#: since the OBDLink EX is USB-powered and reads ~0 V off the car.
LinkState = Literal['no_adapter', 'no_car', 'online']

# Full-snapshot publish cadence and per-PID baseline period (all PIDs currently
# share this rate). The poll loop targets one snapshot per BASELINE_PERIOD_S.
BASELINE_PERIOD_S = 1.0

# Engine-state gate (drain fix). Alternator-charging voltage cleanly separates a
# running engine (~13.8-14.1 V measured) from key-off / key-on-engine-off (~12.0-12.3 V).
VOLTAGE_THRESHOLD_V = 13.2
# While parked: how often to wake, open a connection, and check for a running engine.
# Trades starter-battery drain against how soon logging starts after you turn the key.
WAKE_INTERVAL_S = 10.0
# Consecutive running-cycles below threshold before declaring "parked" — rides out a
# cranking/load voltage sag without dropping the connection.
OFF_DEBOUNCE_CYCLES = 5


@dataclass(frozen=True)
class PidSpec:
    """One pollable PID: its ``obd_readings`` column, OBD command, and rate.

    Attributes:
        column: The ``obd_readings`` / payload column this PID fills.
        command: The python-OBD command to query (typed ``object``; ``obd`` is untyped).
        period_s: Baseline seconds between polls. A future demand overlay would
            shrink this for a PID a live viewer is actively watching (deferred).
    """

    column: str
    command: object
    period_s: float = BASELINE_PERIOD_S


def _pid_specs() -> list[PidSpec]:
    """Build the rate table: ``obd_readings`` column → OBD command.

    Columns mirror ``api/sensor_schema.py``'s ``obd`` metrics, minus ``fuel_rate_lph``
    (no PID on the speed-density Pentastar; derived at read time in ``common/obd.py``).
    This is the only place the command↔column mapping lives.

    Returns:
        The PID specs to poll, in publish/display order.
    """
    table = [
        ('rpm', obd.commands.RPM),
        ('speed_kph', obd.commands.SPEED),
        ('engine_load_pct', obd.commands.ENGINE_LOAD),
        ('throttle_pct', obd.commands.THROTTLE_POS),
        ('coolant_c', obd.commands.COOLANT_TEMP),
        ('intake_c', obd.commands.INTAKE_TEMP),
        ('ambient_air_c', obd.commands.AMBIANT_AIR_TEMP),
        ('map_kpa', obd.commands.INTAKE_PRESSURE),
        ('barometric_kpa', obd.commands.BAROMETRIC_PRESSURE),
        ('fuel_level_pct', obd.commands.FUEL_LEVEL),
        ('voltage_v', obd.commands.CONTROL_MODULE_VOLTAGE),
        ('run_time_s', obd.commands.RUN_TIME),
        ('short_fuel_trim_1_pct', obd.commands.SHORT_FUEL_TRIM_1),
        ('long_fuel_trim_1_pct', obd.commands.LONG_FUEL_TRIM_1),
        ('short_fuel_trim_2_pct', obd.commands.SHORT_FUEL_TRIM_2),
        ('long_fuel_trim_2_pct', obd.commands.LONG_FUEL_TRIM_2),
        ('absolute_load_pct', obd.commands.ABSOLUTE_LOAD),
        ('commanded_equiv_ratio', obd.commands.COMMANDED_EQUIV_RATIO),
    ]
    return [PidSpec(column=column, command=command) for column, command in table]


#: The rate table, built once. ``[s.column for s in PID_SPECS]`` is the snapshot's
#: column set (a test pins it against the shared schema so the two can't drift).
PID_SPECS = _pid_specs()


def default_port() -> str | None:
    """Return the serial port from ``GPS_OBD_PORT`` (default None → auto-scan)."""
    return os.environ.get('GPS_OBD_PORT') or None


def default_protocol() -> str | None:
    """Return the forced protocol id from ``GPS_OBD_PROTOCOL`` (default None → scan)."""
    return os.environ.get('GPS_OBD_PROTOCOL') or None


#: Serial device whose RTS line hardware-keys the transceiver's PTT. Opening it at
#: all — even to fail — transmits.
PTT_PORT = '/dev/digirig'


class PttPortRefused(RuntimeError):
    """Raised when the configured port would key the radio instead of reading OBD."""


def assert_not_ptt_port(port: str | None, ptt_port: str = PTT_PORT) -> None:
    """Refuse to open the radio's PTT-keying serial device.

    RTS asserts on open, so every connect attempt against the Digirig's serial port
    is an unattended transmission. A USB re-enumeration silently swapped the OBD
    adapter and the Digirig between ``ttyUSB0``/``ttyUSB1`` once, leaving this
    reader kerchunking the rig every ~10 s on its parked reconnect wake — hence the
    check here and not only in the unit's ``GPS_OBD_PORT``. Auto-scan is refused
    outright while the device exists, because python-OBD's scan opens every
    ``/dev/ttyUSB*`` in turn.

    Args:
        port: The configured serial device, or None for python-OBD's auto-scan.
        ptt_port: The PTT-keying device to refuse.

    Raises:
        PttPortRefused: If ``port`` resolves to ``ptt_port``, or if an auto-scan was
            requested while ``ptt_port`` exists.
    """
    if not os.path.exists(ptt_port):
        return
    if port is None:
        raise PttPortRefused(
            f'refusing to auto-scan serial ports while {ptt_port} exists — the scan opens '
            f'every /dev/ttyUSB* and RTS on that port keys the radio. Set GPS_OBD_PORT.'
        )
    if os.path.realpath(port) == os.path.realpath(ptt_port):
        raise PttPortRefused(
            f'refusing to open {port}: it resolves to {os.path.realpath(ptt_port)}, the '
            f'radio PTT port ({ptt_port}) — opening it asserts RTS and transmits. '
            f'Point GPS_OBD_PORT at the OBD adapter (/dev/obdlink).'
        )


def numeric(response: object) -> float | None:
    """Extract a scalar magnitude from a python-OBD response.

    Args:
        response: The object returned by ``connection.query`` (a ``pint``-quantity
            wrapper, or null).

    Returns:
        The value as a float, or None if the response is null / not scalar.
    """
    if response is None or response.is_null():  # type: ignore[attr-defined]
        return None
    value = response.value  # type: ignore[attr-defined]
    magnitude = getattr(value, 'magnitude', value)
    try:
        return float(magnitude)
    except (TypeError, ValueError):
        return None


class ObdReader:
    """Owns the real ELM327 serial connection and the engine-state gate.

    Opens lazily (``connect``) so a parked reader holds no connection — the bus
    sleeps and the cheap-clone chatter stops. ``voltage`` answers at the ELM (AT)
    level even with the ECUs asleep, so it doubles as the wake signal.
    """

    def __init__(
        self, port: str | None, baud: int | None, protocol: str | None, fast: bool
    ) -> None:
        self._port = port
        self._baud = baud
        self._protocol = protocol
        self._fast = fast
        self._conn: object | None = None

    def probe(self) -> LinkState:
        """Open the connection if needed and classify the physical link.

        Maps python-OBD's connect-time status: ``NOT_CONNECTED`` (port missing /
        ELM silent) → ``no_adapter``; ``ELM_CONNECTED`` (ELM answered but its
        ``AT RV`` battery-voltage check read < 6 V) → ``no_car``; anything further
        (``OBD_CONNECTED``/``CAR_CONNECTED``) → ``online``. Only ``online`` keeps
        the connection open — whether the ECUs answer PIDs is :meth:`pollable`.

        Returns:
            The link classification for this wake.
        """
        if self._conn is not None:
            return 'online'
        conn = obd.OBD(
            portstr=self._port, baudrate=self._baud, protocol=self._protocol, fast=self._fast
        )
        status = conn.status()
        if status == obd.OBDStatus.NOT_CONNECTED:
            conn.close()
            return 'no_adapter'
        if status == obd.OBDStatus.ELM_CONNECTED:
            conn.close()
            return 'no_car'
        self._conn = conn
        return 'online'

    def pollable(self) -> bool:
        """Return whether the ECUs negotiated a protocol (``CAR_CONNECTED``)."""
        return self._conn is not None and self._conn.status() == obd.OBDStatus.CAR_CONNECTED  # type: ignore[attr-defined]

    def voltage(self) -> float | None:
        """Return the adapter-measured voltage (ATRV), or None if not connected."""
        if self._conn is None:
            return None
        return numeric(self._conn.query(obd.commands.ELM_VOLTAGE))  # type: ignore[attr-defined]

    def poll(self, spec: PidSpec) -> float | None:
        """Query one PID, returning its scalar value or None."""
        if self._conn is None:
            return None
        return numeric(self._conn.query(spec.command))  # type: ignore[attr-defined]

    def close(self) -> None:
        """Close the connection so the CAN bus can sleep."""
        if self._conn is not None:
            self._conn.close()  # type: ignore[attr-defined]
            self._conn = None


class FakeReader:
    """Synthetic reader for desk testing the MQTT → ``obd_readings`` path.

    Always reports a running engine (voltage above threshold) and drifts a plausible
    warm-idle snapshot on a bounded random walk, so the pipeline can be exercised with
    no van, no adapter, and no serial port.
    """

    _BASELINES: dict[str, float] = {
        'rpm': 760.0,
        'speed_kph': 0.0,
        'engine_load_pct': 24.0,
        'throttle_pct': 14.0,
        'coolant_c': 88.0,
        'intake_c': 34.0,
        'ambient_air_c': 24.0,
        'map_kpa': 44.0,
        'barometric_kpa': 98.0,
        'fuel_level_pct': 64.0,
        'voltage_v': 14.1,
        'run_time_s': 0.0,
        'short_fuel_trim_1_pct': 0.8,
        'long_fuel_trim_1_pct': -1.6,
        'short_fuel_trim_2_pct': 1.2,
        'long_fuel_trim_2_pct': -2.0,
        'absolute_load_pct': 22.0,
        'commanded_equiv_ratio': 1.0,
    }

    def __init__(self) -> None:
        self._values = dict(self._BASELINES)
        self._start = time.monotonic()

    def probe(self) -> LinkState:
        """Always ``online`` — the fake car is always plugged in."""
        return 'online'

    def pollable(self) -> bool:
        """Always pollable — the fake ECUs always answer."""
        return True

    def voltage(self) -> float | None:
        """Report alternator-charging voltage so the gate reads 'running'."""
        return 14.1

    def poll(self, spec: PidSpec) -> float | None:
        """Return a drifted plausible value for the spec's column."""
        column = spec.column
        if column == 'run_time_s':
            return round(time.monotonic() - self._start, 1)
        self._values[column] = bounded_step(
            self._values[column], self._BASELINES[column], scale=0.05
        )
        return round(self._values[column], 2)

    def close(self) -> None:
        """No-op; the fake holds no resources."""


class Reader(Protocol):
    """The reader surface :func:`poll_loop` drives (real, fake, or test stub)."""

    def probe(self) -> LinkState:
        """Open the connection if needed and classify the physical link."""

    def pollable(self) -> bool:
        """Return whether the ECUs answer PIDs (``CAR_CONNECTED``)."""

    def voltage(self) -> float | None:
        """Return the adapter-measured battery voltage, or None."""

    def poll(self, spec: PidSpec) -> float | None:
        """Query one PID, returning its scalar value or None."""

    def close(self) -> None:
        """Close the connection so the CAN bus can sleep."""


def _volts(voltage: float | None) -> str:
    """Format a voltage for the heartbeat line (``n/a`` when unreadable)."""
    return f'{voltage:.1f}V' if voltage is not None else 'n/a'


def poll_loop(
    reader: Reader,
    client: Publisher,
    reading_topic: str,
    status_topic: str,
    once: bool,
) -> None:
    """Run the engine-gated poll/publish loop until interrupted.

    Parked: connection closed, wake every ``WAKE_INTERVAL_S`` to classify the link
    and check for a running engine. Running: poll each PID at its due time, publish
    the full current-values snapshot each cycle (fast channels fresh, slow channels
    last-read), and drop back to parked after ``OFF_DEBOUNCE_CYCLES`` sub-threshold
    cycles. The link classification publishes retained on ``status_topic`` on
    transitions only, so an unplugged cable is visible without flapping the broker.

    Args:
        reader: The serial (or fake) reader; the sole owner of the connection.
        client: The connected paho MQTT client.
        reading_topic: ``sensors/<node>/obd`` to publish snapshots to.
        status_topic: ``sensors/<node>/obd/status`` for the retained link state.
        once: Publish a single snapshot (once running) and return — for testing.
    """
    current: dict[str, float | None] = {spec.column: None for spec in PID_SPECS}
    next_due: dict[str, float] = {spec.column: 0.0 for spec in PID_SPECS}
    hb = Heartbeat()
    state = 'parked'
    off_streak = 0
    link: LinkState | None = None

    while True:
        if state == 'parked':
            probed = reader.probe()
            if probed != link:
                publish_status(client, status_topic, probed)
                print(f'link: {probed}', flush=True)
                link = probed
            voltage = reader.voltage() if probed == 'online' else None
            if voltage is not None and voltage >= VOLTAGE_THRESHOLD_V and reader.pollable():
                state = 'running'
                off_streak = 0
                print('engine running — polling', flush=True)
            else:
                reader.close()
                hb.maybe_emit(state=state, link=probed, voltage=_volts(voltage))
                time.sleep(WAKE_INTERVAL_S)
                continue

        cycle_start = time.monotonic()
        voltage = reader.voltage()
        if voltage is None or voltage < VOLTAGE_THRESHOLD_V:
            off_streak += 1
            if off_streak >= OFF_DEBOUNCE_CYCLES:
                state = 'parked'
                reader.close()
                print('engine off — parked', flush=True)
                continue
        else:
            off_streak = 0

        nulls = 0
        for spec in PID_SPECS:
            if cycle_start >= next_due[spec.column]:
                value = reader.poll(spec)
                current[spec.column] = value
                next_due[spec.column] = cycle_start + spec.period_s
                if value is None:
                    nulls += 1

        payload = {'ts': now_canonical(), **current}
        publish_reading(client, reading_topic, json.dumps(payload))
        hb.bump('published')
        hb.bump('nulls', nulls)

        cycle_s = time.monotonic() - cycle_start
        # A cycle slower than the target period means the bus can't keep up at the
        # requested rate — the backpressure signal surfaced in the heartbeat.
        if cycle_s > BASELINE_PERIOD_S:
            hb.bump('saturated')
        hb.maybe_emit(state=state, link=link, voltage=_volts(voltage))

        if once:
            return
        time.sleep(max(0.0, BASELINE_PERIOD_S - cycle_s))


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
        fake_help='Publish synthetic readings instead of reading a real adapter.',
        once_help='Publish a single snapshot (once running) and exit (for testing).',
    )
    parser.add_argument(
        '--port',
        default=default_port(),
        help='Serial port (default $GPS_OBD_PORT; the unit pins the udev symlink '
        '/dev/obdlink). Auto-scan is refused while /dev/digirig exists — the scan opens '
        'every /dev/ttyUSB* and would key the radio.',
    )
    parser.add_argument('--baud', type=int, default=None, help='Baud rate (default: auto).')
    parser.add_argument(
        '--protocol',
        default=default_protocol(),
        help='Force a python-OBD protocol id to skip the scan (default $GPS_OBD_PROTOCOL; '
        "the van is '7', CAN 29/500).",
    )
    parser.add_argument(
        '--fast',
        action='store_true',
        help='Enable python-OBD fast mode (default off; flakier on cheap clones).',
    )
    return parser.parse_args(argv)


class _ProbeNoiseFilter(logging.Filter):
    """Drop python-OBD's expected probe chatter.

    The engine-gated reader deliberately probes the adapter every parked wake, and
    python-OBD logs each expected outcome at ERROR/WARNING: engine off ("ignition is
    off", "unable to connect", "No connection to car"), USB unplugged ("could not
    open port"), and the adapter out of the OBD socket ("OBD2 socket disconnected").
    The reader classifies and reports all of these itself (link transitions + the
    heartbeat's ``link=``), so the per-wake repeats are noise; any other python-OBD
    record passes through.
    """

    _BENIGN = (
        'ignition is off',
        'unable to connect',
        'No connection to car',
        'could not open port',
        'OBD2 socket disconnected',
    )

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False for the known engine-off messages, True otherwise."""
        message = record.getMessage()
        return not any(snippet in message for snippet in self._BENIGN)


def _quiet_obd_probe_logging() -> None:
    """Attach :class:`_ProbeNoiseFilter` to python-OBD's handler.

    python-OBD adds its own stderr handler at import; the filter goes on the handler
    (not the logger) so it catches records propagating up from ``obd.elm327`` /
    ``obd.obd``. Parked observability comes from our own heartbeat (state + link +
    voltage) and the once-per-transition link log line.
    """
    obd_logger = logging.getLogger('obd')
    noise_filter = _ProbeNoiseFilter()
    for handler in obd_logger.handlers:
        handler.addFilter(noise_filter)


def main() -> int:
    """Connect to the broker and run the engine-gated reader loop.

    Returns:
        Process exit code: 0 on graceful shutdown.
    """
    args = parse_args()
    _quiet_obd_probe_logging()
    if not args.fake:
        try:
            assert_not_ptt_port(args.port)
        except PttPortRefused as exc:
            print(f'OBD reader refusing to start: {exc}', file=sys.stderr, flush=True)
            return 2
    reader: ObdReader | FakeReader = (
        FakeReader() if args.fake else ObdReader(args.port, args.baud, args.protocol, args.fast)
    )
    started = f'OBD reader started (node={args.node}, fake={args.fake})'
    with publisher_session(args.node, SENSOR_TYPE, started_msg=started, announce_online=False) as (
        client,
        reading_topic,
        status_topic,
    ):
        try:
            poll_loop(reader, client, reading_topic, status_topic, args.once)
        except KeyboardInterrupt:
            print('OBD reader stopped', flush=True)
        finally:
            reader.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
