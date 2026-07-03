"""Dahua fleet reader parse + poll tests (fixtures from the live Phase-0 probe)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import requests

from api.sensor_schema import READING_TABLES
from sensors.dahua_reader import (
    FLEET,
    FLEET_STREAMS,
    DahuaFleetSensor,
    FakeDahuaFleetSensor,
    parse_clock_offset_s,
    parse_record_mode,
    parse_storage,
    parse_video_loss,
)

# Captured 2026-07-02 by tools/dahua_probe.py against the live fleet (trimmed).
STORAGE_OK = """\
list.info[0].Detail[0].IsError=false
list.info[0].Detail[0].Path=/dev/sda0
list.info[0].Detail[0].TotalBytes=495636709376.000000
list.info[0].Detail[1].IsError=false
list.info[0].Detail[1].Path=/dev/sda1
list.info[0].HealthDataFlag=0
list.info[0].Name=/dev/sda
list.info[0].State=Success
"""

STORAGE_FAULT = """\
list.info[0].Detail[0].IsError=true
list.info[0].Detail[1].IsError=false
list.info[0].State=Failure
"""

VIDEO_LOSS_NONE = 'Error: No Events'
VIDEO_LOSS_TWO = 'channels[0]=2\nchannels[1]=5'

CURRENT_TIME = 'result=2026-07-02 19:09:25'

RECORD_MODE = 'table.RecordMode[0].Mode=0\ntable.RecordMode[0].ModeExtra1=2'


def test_parse_storage() -> None:
    assert parse_storage(STORAGE_OK) == (1, 0)
    assert parse_storage(STORAGE_FAULT) == (0, 1)
    assert parse_storage('') == (None, None)
    assert parse_storage('Error\nBad Request!') == (None, None)


def test_parse_video_loss() -> None:
    assert parse_video_loss(VIDEO_LOSS_NONE) == 0
    assert parse_video_loss(VIDEO_LOSS_TWO) == 2
    assert parse_video_loss('') is None
    assert parse_video_loss('Error\nNot Implemented!') is None


def test_parse_clock_offset() -> None:
    now = datetime(2026, 7, 2, 19, 9, 28, tzinfo=UTC)
    assert parse_clock_offset_s(CURRENT_TIME, now) == -3.0
    assert parse_clock_offset_s('result=garbage', now) is None
    assert parse_clock_offset_s('', now) is None


def test_parse_record_mode() -> None:
    assert parse_record_mode(RECORD_MODE) == 0
    assert parse_record_mode('table.RecordMode[0].Mode=2') == 2
    assert parse_record_mode('Error') is None


BODIES = {
    'storageDevice.cgi': STORAGE_OK,
    'eventManager.cgi': VIDEO_LOSS_NONE,
    'global.cgi': CURRENT_TIME,
    'configManager.cgi': RECORD_MODE,
}


def _patch_fetch(sensor: DahuaFleetSensor, down_hosts: set[str]) -> None:
    """Stub the CGI fetch: fixture bodies, with connection errors for down hosts."""

    def fake_fetch(host: str, path: str) -> str | None:
        if host in down_hosts:
            raise requests.ConnectionError(f'{host} down')
        for key, body in BODIES.items():
            if key in path:
                return body
        return None

    sensor._fetch = fake_fetch  # type: ignore[method-assign]


def test_fleet_read_all_up() -> None:
    sensor = DahuaFleetSensor('pw')
    _patch_fetch(sensor, down_hosts=set())
    readings = sensor.read()
    assert set(readings) == set(FLEET_STREAMS)

    nvr = readings['van-nvr']
    assert nvr is not None
    assert set(nvr) == set(READING_TABLES['nvr']['metrics'])
    assert nvr['hdd_ok'] == 1
    assert nvr['channels_video_loss'] == 0
    assert nvr['clock_offset_s'] is not None

    cam = readings['van-cam-front']
    assert cam is not None
    assert set(cam) == set(READING_TABLES['camera']['metrics'])
    assert cam['online'] == 1
    assert cam['record_mode'] == 0


def test_fleet_read_camera_down_is_a_row() -> None:
    down = {d.host for d in FLEET if d.node == 'van-cam-rear'}
    sensor = DahuaFleetSensor('pw')
    _patch_fetch(sensor, down_hosts=down)
    readings = sensor.read()
    rear = readings['van-cam-rear']
    assert rear == {'online': 0, 'clock_offset_s': None, 'record_mode': None}
    front = readings['van-cam-front']
    assert front is not None and front['online'] == 1


def test_fleet_read_nvr_down_is_dropped() -> None:
    sensor = DahuaFleetSensor('pw')
    _patch_fetch(sensor, down_hosts={'192.168.42.50'})
    readings = sensor.read()
    assert readings['van-nvr'] is None
    cam = readings['van-cam-front']
    assert cam is not None and cam['online'] == 1


@pytest.mark.parametrize('node', sorted(FLEET_STREAMS))
def test_fake_sensor_matches_schema(node: str) -> None:
    reading = FakeDahuaFleetSensor().read()[node]
    assert reading is not None
    assert set(reading) == set(READING_TABLES[FLEET_STREAMS[node]]['metrics'])
