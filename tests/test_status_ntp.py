"""Tests for the /api/ntp status endpoint (api/routes/status_ntp.py).

Also covers the chrony output parsers, which were previously untested, by feeding
``proc.run`` canned ``chronyc`` output keyed on the command.
"""

from __future__ import annotations

import pytest

_TRACKING = """\
Reference ID    : C0A82A01 (gps.example)
Stratum         : 1
Ref time (UTC)  : Sun Jun 28 00:00:00 2026
System time     : 0.000001234 seconds fast of NTP time
Last offset     : +0.000000100 seconds
RMS offset      : 0.000005678 seconds
Frequency       : 1.0 ppm slow
Leap status     : Normal
"""

_SOURCES = """\
MS Name/IP address         Stratum Poll Reach LastRx Last sample
===============================================================================
#* GPS                           0   4   377    10   +12ns[  +12ns] +/-  500ns
^- 1.2.3.4                       2   6   377    55   -1ms[   -1ms] +/-   20ms
"""


@pytest.fixture
def fake_host(monkeypatch):
    """Feed the NTP route canned chrony/systemctl/ss output for a healthy Pi."""

    def fake_run(cmd, timeout=10):
        if cmd[:2] == ['chronyc', 'tracking']:
            return 0, _TRACKING, ''
        if cmd[:2] == ['chronyc', 'sources']:
            return 0, _SOURCES, ''
        if cmd[0] == 'systemctl' and cmd[1] == 'is-active':
            return (0, 'active\n', '') if cmd[2] == 'chrony' else (3, 'inactive\n', '')
        if cmd[0] == 'ss':
            return 0, 'UNCONN 0 0 0.0.0.0:123 0.0.0.0:*\n', ''
        return -1, '', 'unexpected'

    import api.routes.status_ntp as ntp_mod

    monkeypatch.setattr(ntp_mod.proc, 'run', fake_run)


def test_ntp_api_healthy(client, fake_host):
    """A synced PPS-less Pi: all checks pass, tracking + sources parse."""
    resp = client.get('/api/ntp')
    assert resp.status_code == 200
    data = resp.get_json()

    assert data['overall_ok'] is True
    assert data['service_state'] == 'active'
    assert data['serving'] is True
    assert data['tracking']['synced'] is True
    assert data['tracking']['reference'] == 'gps.example'
    assert data['tracking']['stratum'] == 1
    assert data['tracking']['offset_ms'] == pytest.approx(0.001234)

    gps = data['gps_source']
    assert gps is not None and gps['name'] == 'GPS' and gps['selected'] is True
    assert data['pps_mode'] is False
    assert all(c['ok'] for c in data['checks'])
    assert {'name', 'ok'} <= data['checks'][0].keys()


def test_ntp_api_no_chrony(client):
    """Off-Pi (no chrony): endpoint still returns a well-formed failing document."""
    data = client.get('/api/ntp').get_json()
    assert data['overall_ok'] is False
    assert data['tracking']['synced'] is False
    assert data['sources'] == []
    assert isinstance(data['checks'], list)
