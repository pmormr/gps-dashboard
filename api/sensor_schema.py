"""Shared per-type reading-table spec for the sensor platform.

Single source of truth for "which SQLite table and which metric columns back each
sensor type". Both the MQTT ingest **writer** (``mqttbus/ingest.py``) and the
read/display **routes** (``api/routes/sensors.py``) drive off this map, so adding a
sensor stream is one table (in ``api.db``) plus one entry
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
            'dew_point_c',
            'abs_humidity_gm3',
            'heat_index_c',
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
    'openwrt': {
        'table': 'openwrt_readings',
        'metrics': [
            'load_1m',
            'mem_used_pct',
            'uptime_s',
            'wan_up',
            'wan_rx_kbps',
            'wan_tx_kbps',
            'wan_ping_ms',
            'halow_rx_kbps',
            'halow_tx_kbps',
            'halow_stations',
            'halow_rssi_dbm',
            'halow_noise_dbm',
            'halow_tx_mbps',
            'halow_rx_mbps',
            'halow_temp_c',
            'dhcp_leases',
            'conntrack_count',
        ],
    },
    'nvr': {
        'table': 'nvr_readings',
        'metrics': [
            'hdd_ok',
            'hdd_err_partitions',
            'hdd_temp_c',
            'hdd_realloc_sectors',
            'hdd_power_on_h',
            'channels_video_loss',
            'cpu_pct',
            'mem_used_pct',
            'clock_offset_s',
        ],
    },
    'camera': {
        'table': 'camera_readings',
        'metrics': [
            'online',
            'cpu_pct',
            'mem_used_pct',
            'uptime_s',
            'clock_offset_s',
            'record_mode',
        ],
    },
    'fridge': {
        'table': 'fridge_readings',
        'metrics': [
            'comp0_temp_c',
            'comp1_temp_c',
            'comp0_set_c',
            'comp1_set_c',
            'comp0_door_open',
            'comp1_door_open',
            'comp0_power',
            'comp1_power',
            'cooler_power',
            'power_source',
            'input_voltage_v',
            'battery_protection',
            'temp_alert_cc',
            'temp_alert_dcm',
            'door_alert',
            'voltage_alert',
            'dc_current_a',
        ],
    },
    'system': {
        'table': 'system_readings',
        'metrics': [
            'cpu_temp_c',
            'load_1m',
            'mem_used_pct',
            'disk_root_pct',
            'disk_nvme_pct',
            'disk_nvme_free_gb',
            'uptime_s',
            'throttled',
            'undervolt_now',
            'freq_capped_now',
            'throttled_now',
            'temp_limit_now',
        ],
    },
}


class HistoryTable(TypedDict):
    """A sensor type's bucket-shaped history spec — the UPSERT companion to
    :class:`ReadingTable`.

    Some devices keep their own windowed history (the fridge's DC power-usage
    buckets) that a poll re-reads whole each cycle: overlapping windows must
    *update* rows keyed by bucket identity, not append near-duplicates like the
    flat reading INSERT. A type with an entry here may carry ``payload_key`` in
    its reading payload — a list of row dicts — and ingest UPSERTs each row into
    ``table`` on ``(sensor_id, *key)``. Adding a bucket-shaped stream stays a
    spec entry, not an ingest code branch.

    Attributes:
        payload_key: Reading-payload key carrying the row list (absent = no-op).
        table: The SQLite history table (created in ``api.db``).
        key: Row-identity columns (TEXT, from each row dict) after ``sensor_id``.
        metrics: Value columns UPSERTed from each row dict (NULL when absent).
    """

    payload_key: str
    table: str
    key: list[str]
    metrics: list[str]


#: ``type -> history spec`` for types that publish bucket-shaped history.
HISTORY_TABLES: dict[str, HistoryTable] = {
    'fridge': {
        'payload_key': 'history',
        'table': 'fridge_history',
        'key': ['span', 'bucket_ts'],
        'metrics': ['dc_current_a'],
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
_PINK = '#f472b6'
_ROSE = '#fb7185'
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
        codec: How an integer-coded column is decoded for display — ``'enum'`` (value
            → label lookup) or ``'bitmask'`` (OR of flag labels), or None for a plain
            numeric metric. Pairs with :attr:`codes`; the client renders the decoded
            text in place of the raw number (these columns are always ``chart=False``).
        codes: The code→label table for :attr:`codec` — value→label for ``'enum'``,
            mask→label for ``'bitmask'`` (the ``0`` key is the all-clear label). None
            when ``codec`` is None. Serialized with string keys over JSON.
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
    codec: str | None
    codes: dict[int, str] | None


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
    codec: str | None = None,
    codes: dict[int, str] | None = None,
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
        'codec': codec,
        'codes': codes,
    }


# Integer-coded state tables. Victron enums are from the Victron D-Bus / Modbus-TCP
# register list (the paths in sensors/victron_reader.py TOPIC_MAP); the Pi throttle
# table is the vcgencmd get_throttled bitmask. Unlisted codes fall back to the raw
# number, so a firmware that adds a state degrades gracefully rather than mislabelling.

#: system/Dc/Battery/State.
_BATTERY_STATE = {0: 'Idle', 1: 'Charging', 2: 'Discharging'}
#: solarcharger/State (MPPT charge stages).
_SOLAR_STATE = {
    0: 'Off',
    2: 'Fault',
    3: 'Bulk',
    4: 'Absorption',
    5: 'Float',
    6: 'Storage',
    7: 'Equalize',
    245: 'Wake-up',
    252: 'Ext. control',
}
#: system/Ac/ActiveIn/Source.
_AC_SOURCE = {0: 'Not available', 1: 'Grid', 2: 'Generator', 3: 'Shore'}
#: vebus/State (VE.Bus inverter/charger state).
_VEBUS_STATE = {
    0: 'Off',
    1: 'Low power',
    2: 'Fault',
    3: 'Bulk',
    4: 'Absorption',
    5: 'Float',
    6: 'Storage',
    7: 'Equalize',
    8: 'Passthru',
    9: 'Inverting',
    10: 'Power assist',
    11: 'Power supply',
    252: 'Bulk protection',
}
#: vebus/Mode.
_VEBUS_MODE = {1: 'Charger only', 2: 'Inverter only', 3: 'On', 4: 'Off'}
#: OpenWrt WAN logical-interface state (ubus network.interface.wan "up").
_WAN_STATE = {0: 'Down', 1: 'Up'}
#: Dahua storage State field, collapsed to a 0/1 health flag.
_HDD_STATE = {0: 'Fault', 1: 'OK'}
#: Camera poll reachability (the camera answered its CGI poll).
_ONLINE_STATE = {0: 'Offline', 1: 'Online'}
#: Dahua RecordMode config enum.
_RECORD_MODE = {0: 'Auto', 1: 'Manual', 2: 'Off'}
#: CFX3 DDMP PowerSourceType.
_FRIDGE_POWER_SOURCE = {0: 'AC', 1: 'DC', 2: 'Solar'}
#: CFX3 battery-protection level (the front-panel LOW/MED/HIGH setting).
_BATTERY_PROTECTION = {0: 'Low', 1: 'Medium', 2: 'High'}
#: CFX3 compartment door state.
_DOOR_STATE = {0: 'Closed', 1: 'Open'}
#: Plain on/off (fridge cooler + per-zone power).
_ON_OFF = {0: 'Off', 1: 'On'}
#: CFX3 alarm flags.
_ALERT_STATE = {0: 'OK', 1: 'Alert'}
#: vcgencmd get_throttled bitmask. Low bits are currently-active; bits 16+ are the
#: sticky "has occurred since boot" flags. The 0 key is the all-clear label.
_THROTTLED_BITS = {
    0: 'OK',
    0x1: 'under-volt (now)',
    0x2: 'freq-capped (now)',
    0x4: 'throttled (now)',
    0x8: 'temp-limit (now)',
    0x10000: 'under-volt',
    0x20000: 'freq-capped',
    0x40000: 'throttled',
    0x80000: 'temp-limit',
}

#: OR of the live (currently-active) throttle bits — the sub-0x10000 keys above (the
#: sticky since-boot twins sit 16 bits left). A live bit set = an active
#: undervolt/throttle condition *now*, which clears on its own; the sticky bits latch
#: until reboot. Derived from :data:`_THROTTLED_BITS` so a new live bit is picked up
#: automatically. This is the mask a "throttled right now?" check wants.
THROTTLE_LIVE_MASK = sum(bit for bit in _THROTTLED_BITS if 0 < bit < 0x10000)


#: ``column -> MetricMeta``. Every column in every :data:`READING_TABLES` table has an
#: entry (a test guards the drift that left Victron unlabelled). Presentation companion
#: to the storage-focused ``READING_TABLES`` above.
METRIC_META: dict[str, MetricMeta] = {
    # BME680 environmental node.
    'temp_c': _m('Temp', '°C', dec=1, color=_RED, convert='c_to_f', group='environment'),
    'humidity_pct': _m('Humidity', '%', dec=1, color=_BLUE, y_range=[0, 100], group='environment'),
    # Derived comfort channels — computed from temp_c + humidity_pct at ingest
    # (common/humidity.py), not sent by the node. Dew point = condensation
    # threshold; absolute humidity = inside-vs-outside comparable moisture mass;
    # heat index = NWS "feels like" (tracks air temp below ~27 °C by definition).
    'dew_point_c': _m(
        'Dew point', '°C', dec=1, color=_YELLOW, convert='c_to_f', group='environment'
    ),
    'abs_humidity_gm3': _m('Abs humidity', 'g/m³', dec=1, color=_PINK, group='environment'),
    'heat_index_c': _m(
        'Heat index', '°C', dec=1, color=_ROSE, convert='c_to_f', group='environment'
    ),
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
    'fuel_level_pct': _m('Fuel', '%', dec=0, color=_CYAN, y_range=[0, 100], group='fuel', smooth=5),
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
    'battery_state': _m(
        'Battery state', dec=0, chart=False, group='battery', codec='enum', codes=_BATTERY_STATE
    ),
    'pv_power': _m('Solar P', 'W', dec=0, color=_AMBER, group='solar'),
    'pv_voltage': _m('Solar V', 'V', dec=1, color=_CYAN, group='solar'),
    'pv_yield_today_kwh': _m('Yield today', 'kWh', dec=2, color=_GREEN, group='solar'),
    'solar_state': _m(
        'Solar state', dec=0, chart=False, group='solar', codec='enum', codes=_SOLAR_STATE
    ),
    'dc_system_power': _m('DC load', 'W', dec=0, color=_BLUE, group='dc'),
    'ac_in_power': _m('AC in', 'W', dec=0, color=_PURPLE, group='ac'),
    'ac_in_current': _m('AC in I', 'A', dec=1, chart=False, group='ac'),
    'ac_in_source': _m('AC source', dec=0, chart=False, group='ac', codec='enum', codes=_AC_SOURCE),
    'ac_consumption_power': _m('AC load', 'W', dec=0, color=_RED, group='ac'),
    'vebus_state': _m(
        'Inverter state', dec=0, chart=False, group='ac', codec='enum', codes=_VEBUS_STATE
    ),
    'vebus_mode': _m(
        'Inverter mode', dec=0, chart=False, group='ac', codec='enum', codes=_VEBUS_MODE
    ),
    # Raspberry Pi host node. `throttled` is a raw vcgencmd bitmask (0 = healthy),
    # shown as a cell like the Victron enum states rather than plotted.
    'cpu_temp_c': _m('CPU temp', '°C', dec=1, color=_RED, convert='c_to_f', group='compute'),
    'load_1m': _m('Load (1m)', dec=2, color=_BLUE, group='compute'),
    'mem_used_pct': _m('Memory', '%', dec=0, color=_GREEN, y_range=[0, 100], group='compute'),
    'uptime_s': _m('Uptime', 's', dec=0, chart=False, convert='s_to_h', group='compute'),
    'throttled': _m(
        'Throttled', dec=0, chart=False, group='compute', codec='bitmask', codes=_THROTTLED_BITS
    ),
    # Live throttle bits split into chartable 0/1 channels (sensors/system_reader.py),
    # so Trends shows *when* each condition was active — the sticky bits in the raw
    # bitmask above only say "since boot". Bucket-averaging reads as duty cycle. A
    # condition too brief for any poll to catch live still pulses one sample: the
    # reader flags a sticky bit newly latching between consecutive polls.
    'undervolt_now': _m('Under-volt', dec=0, color=_YELLOW, y_range=[0, 1], group='compute'),
    'freq_capped_now': _m('Freq-capped', dec=0, color=_PURPLE, y_range=[0, 1], group='compute'),
    'throttled_now': _m('Throttling', dec=0, color=_ORANGE, y_range=[0, 1], group='compute'),
    'temp_limit_now': _m('Temp limit', dec=0, color=_ROSE, y_range=[0, 1], group='compute'),
    # OpenWrt router node (van-edge; multi-target later). load_1m / mem_used_pct /
    # uptime_s are shared with the Pi 'system' stream above. wan_up is interface
    # state; wan_ping_ms is the internet-actually-works signal (NULL when dark).
    'wan_up': _m('WAN', dec=0, chart=False, group='wan', codec='enum', codes=_WAN_STATE),
    'wan_rx_kbps': _m('WAN down', 'kbps', dec=0, color=_BLUE, group='wan'),
    'wan_tx_kbps': _m('WAN up', 'kbps', dec=0, color=_PURPLE, group='wan'),
    'wan_ping_ms': _m('Ping', 'ms', dec=0, color=_GREEN, group='wan'),
    'halow_rssi_dbm': _m('HaLow RSSI', 'dBm', dec=0, color=_AMBER, group='halow'),
    'halow_noise_dbm': _m('HaLow noise', 'dBm', dec=0, chart=False, group='halow'),
    'halow_rx_kbps': _m('HaLow rx', 'kbps', dec=0, color=_CYAN, group='halow'),
    'halow_tx_kbps': _m('HaLow tx', 'kbps', dec=0, color=_ORANGE, group='halow'),
    'halow_rx_mbps': _m('Link rx rate', 'Mbps', dec=1, chart=False, group='halow'),
    'halow_tx_mbps': _m('Link tx rate', 'Mbps', dec=1, chart=False, group='halow'),
    'halow_stations': _m('Stations', dec=0, chart=False, group='halow'),
    'halow_temp_c': _m('Radio temp', '°C', dec=0, color=_RED, convert='c_to_f', group='halow'),
    'dhcp_leases': _m('DHCP leases', dec=0, color=_YELLOW, group='lan'),
    'conntrack_count': _m('Conntrack', dec=0, color=_RED, group='lan'),
    # Dahua fleet (NVR + cameras). clock_offset_s is shared by both device
    # classes (device clock − Pi clock; the cams NTP-sync from the NVR).
    'hdd_ok': _m('HDD', dec=0, chart=False, group='recording', codec='enum', codes=_HDD_STATE),
    'hdd_err_partitions': _m('HDD errors', dec=0, chart=False, group='recording'),
    'hdd_temp_c': _m('HDD temp', '°C', dec=0, color=_ORANGE, convert='c_to_f', group='recording'),
    'hdd_realloc_sectors': _m('Realloc sectors', dec=0, chart=False, group='recording'),
    'hdd_power_on_h': _m('HDD hours', 'h', dec=0, chart=False, group='recording'),
    'cpu_pct': _m('CPU', '%', dec=0, color=_BLUE, y_range=[0, 100], group='compute'),
    'channels_video_loss': _m('Video loss', dec=0, color=_RED, group='recording'),
    'clock_offset_s': _m('Clock drift', 's', dec=1, chart=False, group='health'),
    'online': _m('Camera', dec=0, chart=False, group='health', codec='enum', codes=_ONLINE_STATE),
    'record_mode': _m(
        'Record mode', dec=0, chart=False, group='recording', codec='enum', codes=_RECORD_MODE
    ),
    # Dometic CFX3 fridge node (dual zone; comp0/comp1 labels stay generic because
    # either compartment can run as fridge or freezer). Setpoints and states render
    # as cells; the two measured temps + input voltage are the trend channels.
    'comp0_temp_c': _m('Zone 1', '°C', dec=1, color=_BLUE, convert='c_to_f', group='cooling'),
    'comp1_temp_c': _m('Zone 2', '°C', dec=1, color=_CYAN, convert='c_to_f', group='cooling'),
    'comp0_set_c': _m('Zone 1 set', '°C', dec=1, chart=False, convert='c_to_f', group='cooling'),
    'comp1_set_c': _m('Zone 2 set', '°C', dec=1, chart=False, convert='c_to_f', group='cooling'),
    'comp0_door_open': _m(
        'Zone 1 door', dec=0, chart=False, group='cooling', codec='enum', codes=_DOOR_STATE
    ),
    'comp1_door_open': _m(
        'Zone 2 door', dec=0, chart=False, group='cooling', codec='enum', codes=_DOOR_STATE
    ),
    'comp0_power': _m(
        'Zone 1 power', dec=0, chart=False, group='cooling', codec='enum', codes=_ON_OFF
    ),
    'comp1_power': _m(
        'Zone 2 power', dec=0, chart=False, group='cooling', codec='enum', codes=_ON_OFF
    ),
    'cooler_power': _m('Cooler', dec=0, chart=False, group='cooling', codec='enum', codes=_ON_OFF),
    'power_source': _m(
        'Source', dec=0, chart=False, group='power', codec='enum', codes=_FRIDGE_POWER_SOURCE
    ),
    'input_voltage_v': _m('Input', 'V', dec=1, color=_YELLOW, group='power'),
    'dc_current_a': _m('DC draw', 'A', dec=1, color=_ORANGE, group='power'),
    'battery_protection': _m(
        'Batt protect', dec=0, chart=False, group='power', codec='enum', codes=_BATTERY_PROTECTION
    ),
    'temp_alert_cc': _m(
        'Temp alert', dec=0, chart=False, group='alerts', codec='enum', codes=_ALERT_STATE
    ),
    'temp_alert_dcm': _m(
        'Temp alert (DCM)', dec=0, chart=False, group='alerts', codec='enum', codes=_ALERT_STATE
    ),
    'door_alert': _m(
        'Door alert', dec=0, chart=False, group='alerts', codec='enum', codes=_ALERT_STATE
    ),
    'voltage_alert': _m(
        'Voltage alert', dec=0, chart=False, group='alerts', codec='enum', codes=_ALERT_STATE
    ),
    'disk_root_pct': _m(
        'Disk (root)', '%', dec=0, color=_PURPLE, y_range=[0, 100], group='storage'
    ),
    'disk_nvme_pct': _m('Disk (NVMe)', '%', dec=0, color=_AMBER, y_range=[0, 100], group='storage'),
    'disk_nvme_free_gb': _m('NVMe free', 'GB', dec=0, color=_CYAN, group='storage'),
}
