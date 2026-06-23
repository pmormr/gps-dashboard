"""Tests for OBD fuel-economy derivation (common.obd) and GET /api/obd/economy.

Pins the speed-density derivation, the gap-capped drive integration, and the
endpoint that joins derived fuel (obd_readings) to track distance (track_points).
"""

import math

import pytest

import api.db as db
from common.obd import (
    FUEL_RATE_CONSTANT,
    derive_fuel_rate_lph,
    summarize_drive,
)

# ---- common.obd: derive_fuel_rate_lph -------------------------------------


def test_derive_matches_speed_density_formula():
    # (load/100) × rpm × K, λ defaulting to stoich.
    assert derive_fuel_rate_lph(30.0, 755.0, 1.0) == pytest.approx(
        0.30 * 755.0 * FUEL_RATE_CONSTANT
    )


def test_derive_idle_is_textbook_order_of_magnitude():
    # Warm idle (~30 % abs load, ~755 rpm) should land near the book ~0.7 gal/h.
    gal_per_h = derive_fuel_rate_lph(30.0, 755.0, 0.999) / 3.785411784
    assert 0.5 < gal_per_h < 0.9


def test_derive_lambda_enrichment_raises_rate():
    rich = derive_fuel_rate_lph(50.0, 2000.0, 0.85)
    stoich = derive_fuel_rate_lph(50.0, 2000.0, 1.0)
    assert rich > stoich


def test_derive_missing_inputs_are_none():
    assert derive_fuel_rate_lph(None, 755.0, 1.0) is None
    assert derive_fuel_rate_lph(30.0, None, 1.0) is None


def test_derive_bad_lambda_falls_back_to_stoich():
    base = derive_fuel_rate_lph(30.0, 755.0, 1.0)
    assert derive_fuel_rate_lph(30.0, 755.0, 0.0) == pytest.approx(base)
    assert derive_fuel_rate_lph(30.0, 755.0, None) == pytest.approx(base)


# ---- common.obd: summarize_drive ------------------------------------------


def test_summarize_constant_rate_is_trapezoid_over_time():
    # 3.6 L/h held over 3 s of 1 Hz samples → 3.6 × (3/3600) L.
    samples = [(float(t), 3.6, 10.0) for t in range(4)]
    s = summarize_drive(samples)
    assert s.fuel_litres == pytest.approx(3.6 * 3 / 3600)
    assert s.engine_on_s == pytest.approx(3.0)
    assert s.moving_s == pytest.approx(3.0)
    assert s.idle_s == pytest.approx(0.0)


def test_summarize_skips_engine_off_gap():
    # A 599 s gap between two 1 s bursts must not be charged the pre-gap rate.
    samples = [(0.0, 3.6, 10.0), (1.0, 3.6, 10.0), (600.0, 3.6, 10.0), (601.0, 3.6, 10.0)]
    s = summarize_drive(samples)
    assert s.fuel_litres == pytest.approx(2 * 3.6 / 3600)
    assert s.engine_on_s == pytest.approx(2.0)


def test_summarize_splits_idle_and_moving_by_speed():
    samples = [(0.0, 3.6, 0.0), (1.0, 3.6, 0.0), (2.0, 3.6, 30.0), (3.0, 3.6, 30.0)]
    s = summarize_drive(samples)
    assert s.idle_s == pytest.approx(1.0)
    assert s.moving_s == pytest.approx(2.0)


def test_summarize_none_fuel_breaks_the_interval():
    samples = [(0.0, 3.6, 10.0), (1.0, None, 10.0), (2.0, 3.6, 10.0)]
    s = summarize_drive(samples)
    # Both intervals are bounded by the None sample → nothing integrated.
    assert s.fuel_litres == pytest.approx(0.0)
    assert s.engine_on_s == pytest.approx(0.0)


# ---- GET /api/obd/economy --------------------------------------------------

WINDOW = {'start': '2026-06-20T12:00:00Z', 'end': '2026-06-20T13:00:00Z'}


def _ts(s):
    return f'2026-06-20T12:00:{s:06.3f}Z'


def _obd(conn, ts, *, rpm, speed, load, eqr=1.0):
    conn.execute(
        'INSERT INTO obd_readings '
        '(sensor_id, timestamp, rpm, speed_kph, absolute_load_pct, commanded_equiv_ratio) '
        'VALUES (1, ?, ?, ?, ?, ?)',
        (ts, rpm, speed, load, eqr),
    )


def _track(conn, ts, lat, lon):
    conn.execute(
        'INSERT INTO track_points '
        '(timestamp, lat, lon, kind, n_raw, importance, src_raw_id) '
        "VALUES (?, ?, ?, 'track', 1, 0.0, 0)",
        (ts, lat, lon),
    )


def test_economy_joins_fuel_and_distance(client):
    conn = db.get_connection()
    for i in range(4):
        _obd(conn, _ts(i), rpm=2000.0, speed=30.0, load=50.0)
    _track(conn, _ts(0), 40.0, -105.0)
    _track(conn, _ts(3), 40.01, -105.0)  # ~1112 m north
    conn.commit()
    conn.close()

    body = client.get('/api/obd/economy', query_string=WINDOW).get_json()
    assert body['n_obd_rows'] == 4
    assert body['n_track_points'] == 2
    assert body['distance_m'] == pytest.approx(1112.0, abs=2.0)
    expected_l = 0.50 * 2000.0 * FUEL_RATE_CONSTANT * 3 / 3600
    assert body['fuel_litres'] == pytest.approx(expected_l, rel=1e-2)
    # mpg matches the derived legs, computed unrounded to dodge display rounding.
    expected_mpg = (math.radians(0.01) * 6_371_000.0 / 1609.344) / (expected_l / 3.785411784)
    assert body['mpg'] == pytest.approx(expected_mpg, rel=2e-2)
    assert body['calibrated'] is False


def test_economy_excludes_engine_off_gap_from_fuel(client):
    conn = db.get_connection()
    # Two 1 s bursts 10 min apart: a naive sum would charge the gap.
    _obd(conn, _ts(0), rpm=2000.0, speed=30.0, load=50.0)
    _obd(conn, _ts(1), rpm=2000.0, speed=30.0, load=50.0)
    _obd(conn, '2026-06-20T12:11:00.000Z', rpm=2000.0, speed=30.0, load=50.0)
    _obd(conn, '2026-06-20T12:11:01.000Z', rpm=2000.0, speed=30.0, load=50.0)
    conn.commit()
    conn.close()

    body = client.get('/api/obd/economy', query_string=WINDOW).get_json()
    assert body['engine_on_s'] == pytest.approx(2.0)
    assert body['fuel_litres'] == pytest.approx(
        0.50 * 2000.0 * FUEL_RATE_CONSTANT * 2 / 3600, rel=1e-2
    )


def test_economy_requires_start_and_end(client):
    assert client.get('/api/obd/economy').status_code == 400
    assert (
        client.get('/api/obd/economy', query_string={'start': WINDOW['start']}).status_code == 400
    )
