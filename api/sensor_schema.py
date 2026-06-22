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
}
