"""Derived humidity channels: dew point, absolute humidity, and heat index.

The cabin BME680 reports temperature and *relative* humidity; these helpers
derive the moisture measures that are actually actionable in a van —
dew point (the condensation threshold for walls/windows), absolute
humidity (grams of water per m³, comparable inside-vs-outside regardless of
temperature, i.e. "will venting dry the cabin"), and heat index (what the
cabin *feels* like — the summer parked-in-the-sun number).

Pure math, no I/O. The MQTT ingest (``mqttbus/ingest.py``) enriches bme680
payloads with these before insert, and the ``api.db`` migration backfills
historical rows — both through :func:`with_derived_humidity` /
:func:`dew_point_c` / :func:`absolute_humidity_gm3`, so the derivation has
exactly one implementation.

Formulas: Magnus equation with the Sonntag 1990 coefficients (a=17.62,
b=243.12 °C, es0=6.112 hPa), accurate to ~0.1 °C over roughly −45..60 °C —
far tighter than the BME680's ±3 % RH element. Pressure correction is a
sub-1 % effect and deliberately omitted. Heat index is the NWS algorithm
(Steadman simple formula, Rothfusz regression + adjustments when ≥ 80 °F),
computed in its native °F and returned in °C.
"""

from __future__ import annotations

import math

_MAGNUS_A = 17.62
_MAGNUS_B = 243.12
_ES0_HPA = 6.112
_KELVIN = 273.15

#: Water-vapor gas constant rearranged for g/m³ from hPa and K:
#: AH = 100·e/(Rw·T) with Rw=461.5 J/(kg·K) → 216.68·e[hPa]/T[K] in g/m³.
_AH_COEFF = 216.68


def saturation_vapor_pressure_hpa(temp_c: float) -> float:
    """Saturation water-vapor pressure over liquid water, in hPa.

    Args:
        temp_c: Air temperature in °C.

    Returns:
        Saturation vapor pressure in hPa (Magnus/Sonntag form).
    """
    return _ES0_HPA * math.exp(_MAGNUS_A * temp_c / (_MAGNUS_B + temp_c))


def dew_point_c(temp_c: float | None, rh_pct: float | None) -> float | None:
    """Dew-point temperature in °C from temperature and relative humidity.

    Args:
        temp_c: Air temperature in °C, or None when the reading is absent.
        rh_pct: Relative humidity in percent, or None when absent.

    Returns:
        Dew point in °C (Magnus inversion), or None when either input is
        missing or RH is out of the physically meaningful (0, 100] range.
    """
    if temp_c is None or rh_pct is None or not 0 < rh_pct <= 100:
        return None
    gamma = math.log(rh_pct / 100) + _MAGNUS_A * temp_c / (_MAGNUS_B + temp_c)
    return _MAGNUS_B * gamma / (_MAGNUS_A - gamma)


def absolute_humidity_gm3(temp_c: float | None, rh_pct: float | None) -> float | None:
    """Absolute humidity (water-vapor density) in g/m³.

    Args:
        temp_c: Air temperature in °C, or None when the reading is absent.
        rh_pct: Relative humidity in percent, or None when absent.

    Returns:
        Water-vapor mass per volume in g/m³, or None when either input is
        missing or RH is out of the physically meaningful (0, 100] range.
    """
    if temp_c is None or rh_pct is None or not 0 < rh_pct <= 100:
        return None
    e_hpa = saturation_vapor_pressure_hpa(temp_c) * rh_pct / 100
    return _AH_COEFF * e_hpa / (temp_c + _KELVIN)


def heat_index_c(temp_c: float | None, rh_pct: float | None) -> float | None:
    """Heat index ("feels like" temperature) in °C, per the NWS algorithm.

    Uses Steadman's simple formula, replaced by the Rothfusz regression with
    the NWS low-RH and high-RH adjustments when the simple result reaches
    80 °F. Below that threshold the index intentionally tracks close to the
    air temperature — that is the NWS definition, not a degenerate case.

    Args:
        temp_c: Air temperature in °C, or None when the reading is absent.
        rh_pct: Relative humidity in percent, or None when absent.

    Returns:
        Heat index in °C, or None when either input is missing or RH is out
        of the physically meaningful (0, 100] range.
    """
    if temp_c is None or rh_pct is None or not 0 < rh_pct <= 100:
        return None
    t = temp_c * 9 / 5 + 32
    rh = rh_pct
    hi = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + rh * 0.094)
    if hi >= 80.0:
        hi = (
            -42.379
            + 2.04901523 * t
            + 10.14333127 * rh
            - 0.22475541 * t * rh
            - 6.83783e-3 * t * t
            - 5.481717e-2 * rh * rh
            + 1.22874e-3 * t * t * rh
            + 8.5282e-4 * t * rh * rh
            - 1.99e-6 * t * t * rh * rh
        )
        if rh < 13 and 80 <= t <= 112:
            hi -= (13 - rh) / 4 * math.sqrt((17 - abs(t - 95)) / 17)
        elif rh > 85 and 80 <= t <= 87:
            hi += (rh - 85) / 10 * (87 - t) / 5
    return (hi - 32) * 5 / 9


def with_derived_humidity(payload: dict[str, object]) -> dict[str, object]:
    """Return a bme680 payload copy with derived comfort channels added.

    Computes ``dew_point_c``, ``abs_humidity_gm3``, and ``heat_index_c`` from
    the payload's ``temp_c``/``humidity_pct``. Node-supplied values for the
    derived keys, if ever present, win over the local computation; inputs that
    are missing or non-numeric yield None (stored as NULL, matching every
    other absent metric).

    Args:
        payload: Decoded bme680 reading JSON (not mutated).

    Returns:
        A shallow copy with the derived keys filled in.
    """
    temp = payload.get('temp_c')
    rh = payload.get('humidity_pct')
    if not isinstance(temp, int | float) or not isinstance(rh, int | float):
        temp, rh = None, None
    out = dict(payload)
    out.setdefault('dew_point_c', dew_point_c(temp, rh))
    out.setdefault('abs_humidity_gm3', absolute_humidity_gm3(temp, rh))
    out.setdefault('heat_index_c', heat_index_c(temp, rh))
    return out
