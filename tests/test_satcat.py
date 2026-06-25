"""Tests for the pure parsing helpers in the SATCAT metadata cache."""

from __future__ import annotations

from common.satcat import _f, _parse, _prn_from_name


def test_prn_from_name_numeric() -> None:
    """A trailing ``(PRN nn)`` yields the bare PRN number."""
    assert _prn_from_name('GPS BIIR-5  (PRN 22)') == '22'
    assert _prn_from_name('QZS-2 (QZSS/PRN 194)') == '194'
    assert _prn_from_name('ASTRA 5B (EGNOS/PRN 123)') == '123'


def test_prn_from_name_lettered_and_absent() -> None:
    """A lettered PRN keeps its prefix; a name without PRN returns None."""
    assert _prn_from_name('FOO (PRN E11)') == 'E11'
    assert _prn_from_name('GSAT0210 (GALILEO 14)') is None


def test_f_coerces_blanks_to_none() -> None:
    """Numeric coercion maps blanks and junk to None, numbers through."""
    assert _f('') is None
    assert _f(None) is None
    assert _f('abc') is None
    assert _f(54.85) == 54.85
    assert _f('718') == 718.0


def test_parse_builds_satmeta() -> None:
    """A raw SATCAT record parses into the typed metadata fields."""
    rec = {
        'OBJECT_NAME': 'GPS BIIR-5  (PRN 22)',
        'OBJECT_ID': '2000-040A',
        'NORAD_CAT_ID': 26407,
        'OBJECT_TYPE': 'PAY',
        'OPS_STATUS_CODE': '+',
        'OWNER': 'US',
        'LAUNCH_DATE': '2000-07-16',
        'LAUNCH_SITE': 'AFETR',
        'DECAY_DATE': '',
        'PERIOD': 718,
        'INCLINATION': 54.85,
        'APOGEE': 20504,
        'PERIGEE': 19861,
        'RCS': 5.24,
        'ORBIT_TYPE': 'ORB',
    }
    m = _parse(rec)
    assert m.norad == 26407
    assert m.prn == '22'
    assert m.owner == 'US'
    assert m.inclination_deg == 54.85
    assert m.decay_date == ''
