"""OpenWrt reader parse + delta-state tests (fixtures from the live van-edge probe)."""

from __future__ import annotations

import pytest

from api.sensor_schema import READING_TABLES
from sensors import openwrt_reader
from sensors.openwrt_reader import (
    FakeOpenwrtSensor,
    OpenwrtSensor,
    ThroughputState,
    build_remote_script,
    parse_assoclist,
    parse_first_float,
    parse_int,
    parse_iwinfo_signal,
    parse_marked_sections,
    parse_net_dev,
    parse_ping_avg_ms,
    parse_wan_up,
    poll_sections,
)

# Captured 2026-07-02 by tools/openwrt_probe.py against van-edge (trimmed;
# interior whitespace compacted — the parser splits on any whitespace).
NET_DEV = """\
Inter-| Receive |  Transmit
 face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls
    lo: 181034 2082 0 0 0 0 0 0 181034 2082 0 0 0 0 0 0
   wan: 81280827 445991 0 0 0 0 0 0 74706465 224301 0 0 0 0 0 0
 wlan0: 240862570 2226036 0 0 0 0 0 0 6129037027 4671323 0 0 0 0 0 0
"""

IWINFO_INFO = """\
wlan0     ESSID: "vannet"
          Access Point: 50:2E:91:D2:C6:69
          Mode: Master  Channel: 12 (908.000 MHz)  HT Mode: HT20
          Tx-Power: 23 dBm  Link Quality: 56/70
          Signal: -54 dBm  Noise: -96 dBm
          Bit Rate: 19.5 MBit/s
"""

ASSOCLIST = """\
78:72:64:EA:C1:8E  -54 dBm / -96 dBm (SNR 42)  200 ms ago
\tRX: 32.5 MBit/s, MCS 7, 8MHz                  295294 Pkts.
\tTX: 19.5 MBit/s, MCS 4, 8MHz                  593645 Pkts.
\texpected throughput: 19.5 MBit/s
"""

PING_OK = """\
PING 8.8.8.8 (8.8.8.8): 56 data bytes

--- 8.8.8.8 ping statistics ---
3 packets transmitted, 3 packets received, 0% packet loss
round-trip min/avg/max = 36.751/43.140/52.181 ms
"""

WAN_STATUS = '{"up": true, "uptime": 120025, "l3_device": "wan"}'

MEMINFO = 'MemTotal:         249628 kB\nMemAvailable:     144532 kB\n'


def test_parse_net_dev() -> None:
    counters = parse_net_dev(NET_DEV)
    assert counters['wan'] == (81280827, 74706465)
    assert counters['wlan0'] == (240862570, 6129037027)
    assert 'face' not in counters


def test_parse_iwinfo_signal() -> None:
    assert parse_iwinfo_signal(IWINFO_INFO) == (-54.0, -96.0)
    assert parse_iwinfo_signal('No such wireless device: wlan0') == (None, None)


def test_parse_assoclist() -> None:
    assert parse_assoclist(ASSOCLIST) == (1, 32.5, 19.5)
    assert parse_assoclist('') == (0, None, None)


def test_parse_ping() -> None:
    assert parse_ping_avg_ms(PING_OK) == 43.140
    assert parse_ping_avg_ms('3 packets transmitted, 0 packets received') is None


def test_parse_wan_up() -> None:
    assert parse_wan_up(WAN_STATUS) == 1
    assert parse_wan_up('{"up": false}') == 0
    assert parse_wan_up('Command failed') is None


def test_scalar_parses() -> None:
    assert parse_first_float('0.00 0.03 0.05 1/111 2745') == 0.0
    assert parse_first_float('') is None
    assert parse_int(' 9\n') == 9
    assert parse_int('nope') is None


def test_throughput_state(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter([100.0, 110.0, 120.0, 130.0])
    monkeypatch.setattr(openwrt_reader.time, 'monotonic', lambda: next(clock))
    state = ThroughputState()

    assert state.rate_kbps('wan', {'wan': (1000, 500)}) == (None, None)
    rx, tx = state.rate_kbps('wan', {'wan': (11000, 3000)})
    assert rx == pytest.approx(8.0)  # 10000 B over 10 s = 8 kbps
    assert tx == pytest.approx(2.0)
    # Counter went backwards (reboot): None this cycle, clean delta next.
    assert state.rate_kbps('wan', {'wan': (400, 100)}) == (None, None)
    assert state.rate_kbps('wan', {'wan': (10400, 2600)}) == (
        pytest.approx(8.0),
        pytest.approx(2.0),
    )


def test_throughput_missing_iface() -> None:
    state = ThroughputState()
    assert state.rate_kbps('wan', {}) == (None, None)


def _marked_output(bodies: dict[str, str]) -> str:
    """Render a fake remote output: each section echoes its fixture body."""
    sections = poll_sections('wan', 'wlan0')
    lines = []
    for name in sections:
        body = bodies.get(name, '')
        lines.append(f'{openwrt_reader.BEGIN_MARK} {name}')
        if body:
            lines.append(body.rstrip('\n'))
        lines.append(f'{openwrt_reader.END_MARK} {name} rc=0')
    return '\n'.join(lines) + '\n'


FULL_BODIES = {
    'loadavg': '0.00 0.03 0.05 1/111 2745',
    'meminfo': MEMINFO,
    'uptime': '120060.33 468687.53',
    'net_dev': NET_DEV,
    'wan_status': WAN_STATUS,
    'iwinfo_info': IWINFO_INFO,
    'assoclist': ASSOCLIST,
    'dhcp_leases': '9',
    'conntrack': '143',
    'ping': PING_OK,
}


def test_sensor_read_full_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    sensor = OpenwrtSensor()
    monkeypatch.setattr(sensor, '_run_remote', lambda: _marked_output(FULL_BODIES))
    reading = sensor.read()
    assert reading is not None
    assert set(reading) == set(READING_TABLES['openwrt']['metrics'])
    assert reading['load_1m'] == 0.0
    assert reading['mem_used_pct'] == pytest.approx(42.1, abs=0.1)
    assert reading['uptime_s'] == 120060.33
    assert reading['wan_up'] == 1
    assert reading['wan_ping_ms'] == 43.140
    assert reading['halow_rssi_dbm'] == -54.0
    assert reading['halow_noise_dbm'] == -96.0
    assert reading['halow_stations'] == 1
    assert reading['halow_rx_mbps'] == 32.5
    assert reading['halow_tx_mbps'] == 19.5
    assert reading['dhcp_leases'] == 9
    assert reading['conntrack_count'] == 143
    # First poll: no previous counters, so throughput is NULL.
    assert reading['wan_rx_kbps'] is None and reading['halow_rx_kbps'] is None

    second = sensor.read()
    assert second is not None and second['wan_rx_kbps'] is not None


def test_sensor_read_radio_down(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies = dict(FULL_BODIES, iwinfo_info='', assoclist='')
    sensor = OpenwrtSensor()
    monkeypatch.setattr(sensor, '_run_remote', lambda: _marked_output(bodies))
    reading = sensor.read()
    assert reading is not None
    assert reading['halow_rssi_dbm'] is None
    assert reading['halow_stations'] is None  # radio sections empty: unknown, not 0
    assert reading['load_1m'] == 0.0  # the rest of the snapshot stands


def test_sensor_read_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    sensor = OpenwrtSensor()
    monkeypatch.setattr(sensor, '_run_remote', lambda: None)
    assert sensor.read() is None


def test_marker_round_trip() -> None:
    script = build_remote_script({'a': 'echo hi', 'b': 'true'})
    assert script.count(openwrt_reader.BEGIN_MARK) == 2
    parsed = parse_marked_sections(
        f'{openwrt_reader.BEGIN_MARK} a\nhi\n{openwrt_reader.END_MARK} a rc=0\n'
        f'{openwrt_reader.BEGIN_MARK} b\n{openwrt_reader.END_MARK} b rc=1\n'
    )
    assert parsed == {'a': (0, 'hi'), 'b': (1, '')}


def test_fake_sensor_matches_schema() -> None:
    reading = FakeOpenwrtSensor().read()
    assert reading is not None
    assert set(reading) == set(READING_TABLES['openwrt']['metrics'])
    assert reading['wan_up'] == 1
