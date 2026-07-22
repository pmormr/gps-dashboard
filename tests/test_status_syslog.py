"""Tests for the syslog-ng stats parser behind /api/syslog."""

from __future__ import annotations

from api.routes.status_syslog import _parse_stats

# A representative `syslog-ng-ctl stats` dump: the Graylog destination counters,
# both source counters (relay + local), and unrelated rows that must be ignored.
_SAMPLE = '\n'.join(
    [
        'SourceName;SourceId;SourceInstance;State;Type;Number',
        'dst.network;d_graylog#0;tcp,rex-nas.rex.pmormr.com:514;a;queued;42',
        'dst.network;d_graylog#0;tcp,rex-nas.rex.pmormr.com:514;a;dropped;0',
        'dst.network;d_graylog#0;tcp,rex-nas.rex.pmormr.com:514;a;written;121095',
        'dst.network;d_graylog#0;tcp,rex-nas.rex.pmormr.com:514;a;processed;121137',
        'dst.network;d_graylog#0;tcp,rex-nas.rex.pmormr.com:514;a;eps_last_1h;2',
        'destination;d_graylog;;a;processed;121154',
        'source;s_net;;a;processed;11',
        'src.network;s_net;afsocket_sd.(stream,AF_INET(0.0.0.0:514));a;connections;1',
        'source;s_src;;a;processed;120000',
        'destination;d_mail;;a;processed;0',
    ]
)


def test_parse_stats_pulls_graylog_and_source_counters():
    parsed = _parse_stats(_SAMPLE)
    assert parsed['queued'] == 42
    assert parsed['dropped'] == 0
    assert parsed['written'] == 121095
    assert parsed['eps_1h'] == 2
    assert parsed['relayed'] == 11
    assert parsed['local'] == 120000


def test_parse_stats_missing_metrics_are_none():
    parsed = _parse_stats('source;s_src;;a;processed;5')
    assert parsed['local'] == 5
    assert parsed['queued'] is None
    assert parsed['dropped'] is None
    assert parsed['written'] is None
    assert parsed['relayed'] is None


def test_parse_stats_skips_malformed_and_nonnumeric_rows():
    parsed = _parse_stats('too;few;fields\ndst.network;d_graylog#0;x;a;queued;notanumber\n\n')
    assert parsed['queued'] is None


def test_syslog_endpoint_shape(client):
    """The route is wired and returns the check-page shape, even with no syslog-ng.

    Values are environment-dependent (the route shells out to ss/systemctl/
    syslog-ng-ctl), but the document shape must be stable for the frontend.
    """
    resp = client.get('/api/syslog')
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) >= {'overall_ok', 'checks', 'service_state', 'listening', 'destination'}
    assert 'stats' in body
    assert len(body['checks']) >= 3
    assert set(body['listening']) == {'udp', 'tcp'}
