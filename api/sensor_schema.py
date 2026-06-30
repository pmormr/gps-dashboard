"""Shared per-type reading-table spec for the sensor platform.

Single source of truth for "which SQLite table and which metric columns back each
sensor type". Both the MQTT ingest **writer** (``mqttbus/ingest.py``) and the
read/display **routes** (``api/routes/sensors.py``) drive off this map, so adding a
sensor stream is one schema migration (the table in ``api.db``) plus one entry
here — the write path, registry, latest-reading, history, and charts all follow.

Lives under ``api`` (not ``mqttbus``) to keep the dependency direction one-way:
``mqttbus`` and the routes both import ``api``; ``api`` imports neither back.

The ``metrics`` list does double duty — it is the column set the ingest writer
inserts (it pulls ``payload[col]`` for each, NULL when a column is absent that
cycle) **and** the display order the charts render — so the two can never drift.

:data:`METRIC_META` is the presentation companion: per-column label / unit / scale,
served to the ``/sensors`` viewer by ``/api/sensors`` so display metadata has one
home instead of a parallel hardcoded map in the frontend. It is keyed by column name
(shared names share a row) and read only by the read route — ``mqttbus`` ingest
imports ``READING_TABLES`` alone, keeping storage and presentation concerns apart.
"""

from __future__ import annotations

from typing import TypedDict


class ReadingTable(TypedDict):
    """A sensor type's storage table and its metric columns.

    Attributes:
        table: The SQLite table holding this type's readings.
        metrics: Metric column names — the ingest insert set and the display order.
    """

    table: str
    metrics: list[str]


#: ``type -> {table, metrics}``. Adding a type: create its table in ``api.db`` and
#: add an entry here. ``metrics`` is the insert column set *and* the display order.
READING_TABLES: dict[str, ReadingTable] = {
    'bme680': {
        'table': 'bme680_readings',
        'metrics': [
            'temp_c',
            'humidity_pct',
            'pressure_hpa',
            'iaq',
            'iaq_accuracy',
            'co2_equivalent',
            'breath_voc_equivalent',
            'gas_ohms',
        ],
    },
    'obd': {
        'table': 'obd_readings',
        'metrics': [
            'rpm',
            'speed_kph',
            'engine_load_pct',
            'throttle_pct',
            'coolant_c',
            'intake_c',
            'ambient_air_c',
            'map_kpa',
            'barometric_kpa',
            'fuel_level_pct',
            'fuel_rate_lph',
            'voltage_v',
            'run_time_s',
            'short_fuel_trim_1_pct',
            'long_fuel_trim_1_pct',
            'short_fuel_trim_2_pct',
            'long_fuel_trim_2_pct',
            'absolute_load_pct',
            'commanded_equiv_ratio',
        ],
    },
    'victron': {
        'table': 'victron_readings',
        'metrics': [
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
        ],
    },
}


# Display palette — the prior hardcoded ``sensors.js`` colors, moved server-side so
# nothing regresses visually. Uncharted metrics keep the default grey.
_RED = '#f87171'
_BLUE = '#38bdf8'
_PURPLE = '#a78bfa'
_GREEN = '#34d399'
_ORANGE = '#fb923c'
_AMBER = '#fbbf24'
_CYAN = '#22d3ee'
_YELLOW = '#facc15'
_GREY = '#94a3b8'


class MetricMeta(TypedDict):
    """Presentation metadata for one metric column.

    The single source of truth for how a metric is *shown* — label, unit, and scale.
    Served to the client by ``/api/sensors`` so the viewer renders from data instead
    of a parallel hardcoded map. Keyed by column name in :data:`METRIC_META`; columns
    that share a name (a future second ``temp_c``) share a row.

    Attributes:
        label: Short human label (e.g. ``'Coolant'``).
        unit: Unit suffix shown after the value (``''`` when unitless).
        dec: Decimal places for display.
        chart: Whether to draw a trend chart. False ⇒ current-value cell only — e.g.
            an enum-coded state column that means nothing plotted as a continuous line.
        color: Trend-line CSS color.
        convert: Alt-unit conversion id for a secondary readout (``'c_to_f'``,
            ``'kph_to_mph'``, ``'s_to_h'``), or None for no conversion.
        y_range: Fixed ``[min, max]`` chart y-axis, or None to autoscale. Pinning a
            bounded channel (a 0–100 % metric) stops in-band noise from filling the
            chart and reading as turbulence.
        group: Logical grouping key (``'battery'``, ``'solar'``, …) for sectioning the
            current-values grid (and, later, overlay charts).
        smooth: Default trend-chart smoothing window (moving-average buckets) for this
            channel; 0 = none. A floor the global Trends control can raise but not
            lower, so an intrinsically noisy channel (fuel-level slosh) reads cleanly
            without dragging every other series through the same filter.
    """

    label: str
    unit: str
    dec: int
    chart: bool
    color: str
    convert: str | None
    y_range: list[float] | None
    group: str
    smooth: int


def _m(
    label: str,
    unit: str = '',
    *,
    dec: int = 0,
    chart: bool = True,
    color: str = _GREY,
    convert: str | None = None,
    y_range: list[float] | None = None,
    group: str = '',
    smooth: int = 0,
) -> MetricMeta:
    """Build a :class:`MetricMeta`, defaulting the optional presentation fields."""
    return {
        'label': label,
        'unit': unit,
        'dec': dec,
        'chart': chart,
        'color': color,
        'convert': convert,
        'y_range': y_range,
        'group': group,
        'smooth': smooth,
    }


#: ``column -> MetricMeta``. Every column in every :data:`READING_TABLES` table has an
#: entry (a test guards the drift that left Victron unlabelled). Presentation companion
#: to the storage-focused ``READING_TABLES`` above.
METRIC_META: dict[str, MetricMeta] = {
    # BME680 environmental node.
    'temp_c': _m('Temp', '°C', dec=1, color=_RED, convert='c_to_f', group='environment'),
    'humidity_pct': _m('Humidity', '%', dec=1, color=_BLUE, y_range=[0, 100], group='environment'),
    'pressure_hpa': _m('Pressure', 'hPa', dec=1, color=_PURPLE, group='environment'),
    'iaq': _m('IAQ', dec=0, color=_GREEN, group='environment'),
    'iaq_accuracy': _m('IAQ acc', '/3', dec=0, chart=False, group='environment'),
    'co2_equivalent': _m('CO₂-eq', 'ppm', dec=0, color=_AMBER, group='environment'),
    'breath_voc_equivalent': _m('Breath VOC', 'ppm', dec=2, color=_ORANGE, group='environment'),
    'gas_ohms': _m('Gas', 'Ω', dec=0, color=_CYAN, group='environment'),
    # OBD-II van node.
    'rpm': _m('RPM', 'rpm', dec=0, color=_RED, y_range=[0, 6000], group='engine'),
    'speed_kph': _m('Speed', 'km/h', dec=0, color=_BLUE, convert='kph_to_mph', group='engine'),
    'engine_load_pct': _m('Load', '%', dec=0, color=_GREEN, y_range=[0, 100], group='engine'),
    'throttle_pct': _m('Throttle', '%', dec=0, color=_PURPLE, y_range=[0, 100], group='engine'),
    'map_kpa': _m('MAP', 'kPa', dec=0, color=_AMBER, group='engine'),
    'barometric_kpa': _m('Baro', 'kPa', dec=0, chart=False, group='engine'),
    'absolute_load_pct': _m('Abs load', '%', dec=0, chart=False, y_range=[0, 100], group='engine'),
    'run_time_s': _m('Run time', 's', dec=0, chart=False, group='engine'),
    'coolant_c': _m('Coolant', '°C', dec=0, color=_ORANGE, convert='c_to_f', group='temps'),
    'intake_c': _m('Intake', '°C', dec=0, chart=False, convert='c_to_f', group='temps'),
    'ambient_air_c': _m('Ambient', '°C', dec=0, chart=False, convert='c_to_f', group='temps'),
    'fuel_level_pct': _m(
        'Fuel', '%', dec=0, color=_CYAN, y_range=[0, 100], group='fuel', smooth=5
    ),
    'fuel_rate_lph': _m('Fuel rate', 'L/h', dec=1, chart=False, group='fuel'),
    'commanded_equiv_ratio': _m('λ cmd', dec=3, chart=False, group='fuel'),
    'short_fuel_trim_1_pct': _m('STFT B1', '%', dec=1, chart=False, group='fuel'),
    'long_fuel_trim_1_pct': _m('LTFT B1', '%', dec=1, chart=False, group='fuel'),
    'short_fuel_trim_2_pct': _m('STFT B2', '%', dec=1, chart=False, group='fuel'),
    'long_fuel_trim_2_pct': _m('LTFT B2', '%', dec=1, chart=False, group='fuel'),
    'voltage_v': _m('Battery', 'V', dec=1, color=_YELLOW, group='electrical'),
    # Victron house-power node (previously rendered through the generic fallback).
    'battery_soc': _m('Battery SoC', '%', dec=0, color=_GREEN, y_range=[0, 100], group='battery'),
    'battery_voltage': _m('Battery V', 'V', dec=2, color=_YELLOW, group='battery'),
    'battery_current': _m('Battery I', 'A', dec=1, color=_ORANGE, group='battery'),
    'battery_power': _m('Battery P', 'W', dec=0, color=_RED, group='battery'),
    'battery_temp_c': _m(
        'Battery temp', '°C', dec=1, chart=False, convert='c_to_f', group='battery'
    ),
    'consumed_ah': _m('Consumed', 'Ah', dec=1, color=_PURPLE, group='battery'),
    'time_to_go_s': _m('Time to go', 's', dec=0, chart=False, convert='s_to_h', group='battery'),
    'battery_state': _m('Battery state', dec=0, chart=False, group='battery'),
    'pv_power': _m('Solar P', 'W', dec=0, color=_AMBER, group='solar'),
    'pv_voltage': _m('Solar V', 'V', dec=1, color=_CYAN, group='solar'),
    'pv_yield_today_kwh': _m('Yield today', 'kWh', dec=2, color=_GREEN, group='solar'),
    'solar_state': _m('Solar state', dec=0, chart=False, group='solar'),
    'dc_system_power': _m('DC load', 'W', dec=0, color=_BLUE, group='dc'),
    'ac_in_power': _m('AC in', 'W', dec=0, color=_PURPLE, group='ac'),
    'ac_in_current': _m('AC in I', 'A', dec=1, chart=False, group='ac'),
    'ac_in_source': _m('AC source', dec=0, chart=False, group='ac'),
    'ac_consumption_power': _m('AC load', 'W', dec=0, color=_RED, group='ac'),
    'vebus_state': _m('Inverter state', dec=0, chart=False, group='ac'),
    'vebus_mode': _m('Inverter mode', dec=0, chart=False, group='ac'),
}
