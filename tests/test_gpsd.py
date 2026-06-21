"""Tests for the pure satellite-constellation resolver in common.gpsd.

``query_gpsd``/``configured_gpsd_device`` are I/O-bound and excluded here;
``constellation`` is a pure lookup with two branches — explicit ``gnssid`` and
the legacy PRN-range fallback — worth pinning.
"""

import pytest

from common.gpsd import constellation


@pytest.mark.parametrize(
    ('gnssid', 'expected'),
    [(0, 'GPS'), (2, 'Galileo'), (3, 'BeiDou'), (6, 'GLONASS'), (5, 'QZSS')],
)
def test_constellation_from_gnssid(gnssid, expected):
    assert constellation({'gnssid': gnssid}) == expected


def test_gnssid_takes_precedence_over_prn():
    # PRN 5 is GPS by range, but an explicit gnssid wins.
    assert constellation({'gnssid': 2, 'PRN': 5}) == 'Galileo'


@pytest.mark.parametrize(
    ('prn', 'expected'),
    [
        (5, 'GPS'),
        (70, 'GLONASS'),
        (130, 'SBAS'),
        (195, 'QZSS'),
        (220, 'BeiDou'),
        (310, 'Galileo'),
    ],
)
def test_constellation_from_prn_fallback(prn, expected):
    assert constellation({'PRN': prn}) == expected


def test_unknown_gnssid_falls_back_to_prn():
    assert constellation({'gnssid': 99, 'PRN': 5}) == 'GPS'


def test_unresolved_is_other():
    assert constellation({}) == 'Other'
    assert constellation({'PRN': 9999}) == 'Other'
