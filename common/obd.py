"""Speed-density fuel-rate derivation + drive integration for the OBD van stream.

The ProMaster's Pentastar is speed-density (no MAF) and exposes no native
fuel-rate PID (``015E``), so litres/hour is **derived** from the SAE absolute
engine-load air-charge relation, RPM, and the commanded lambda. ``obd_readings``
stores ``fuel_rate_lph`` as NULL and the value is derived here at read time, so a
recalibration is a one-constant change that re-scales all logged history (see
:data:`FUEL_RATE_CONSTANT`).

Pure functions, no DB/I/O: the API route queries rows and feeds them in.
"""

from __future__ import annotations

from dataclasses import dataclass

# Collapses the speed-density chain to one multiplier on (load_fraction × rpm):
#   1.184 g/L   air density at SAE STP (25 °C, 101.3 kPa)
#   × 3.6 L     Pentastar displacement
#   / 120       four-stroke air mass per second = charge × rpm / 120 (g/s)
#   / 14.7      stoichiometric gasoline air-fuel ratio (λ = 1)
#   / 745 g/L   gasoline density
#   × 3600      g/s → L/h
# ≈ 0.01168 L/h per (load_fraction × rpm). Validated against idle (~0.6 gal/h,
# textbook) on the 2026-06-22 grocery run; this single number is the calibration
# lever — scale it by (actual ÷ derived) from a tank-to-tank fill-up.
FUEL_RATE_CONSTANT = 1.184 * 3.6 / 120.0 / 14.7 / 745.0 * 3600.0

# Below this speed a sample counts as idle/stopped rather than moving.
MOVING_SPEED_KPH = 1.0
# Sampling is ~1 Hz; cap the integration step so an engine-off gap between trips
# isn't charged the pre-gap fuel rate.
DEFAULT_MAX_GAP_S = 5.0


def derive_fuel_rate_lph(
    absolute_load_pct: float | None,
    rpm: float | None,
    commanded_equiv_ratio: float | None,
) -> float | None:
    """Derive instantaneous fuel rate (L/h) by speed density.

    Args:
        absolute_load_pct: SAE absolute load (PID 0x43), percent.
        rpm: Engine speed (PID 0x0C).
        commanded_equiv_ratio: Commanded lambda (PID 0x44); falsy/≤0 ⇒ assume
            stoichiometric (1.0), the closed-loop default.

    Returns:
        Fuel rate in litres per hour, or ``None`` when load or RPM is missing.
    """
    if absolute_load_pct is None or rpm is None:
        return None
    lam = commanded_equiv_ratio if commanded_equiv_ratio and commanded_equiv_ratio > 0 else 1.0
    return absolute_load_pct / 100.0 * rpm * FUEL_RATE_CONSTANT / lam


@dataclass(frozen=True)
class DriveSummary:
    """Integrated totals over a drive window.

    Attributes:
        fuel_litres: Total derived fuel burned.
        engine_on_s: Sampled seconds the engine was running (gap-capped).
        moving_s: Of ``engine_on_s``, seconds above :data:`MOVING_SPEED_KPH`.
        idle_s: Of ``engine_on_s``, seconds at or below it.
    """

    fuel_litres: float
    engine_on_s: float
    moving_s: float
    idle_s: float


def summarize_drive(
    samples: list[tuple[float, float | None, float | None]],
    max_gap_s: float = DEFAULT_MAX_GAP_S,
    moving_kph: float = MOVING_SPEED_KPH,
) -> DriveSummary:
    """Integrate fuel and engine-on time over time-ordered samples.

    Trapezoidal integration of fuel rate (L/h) over each consecutive interval,
    skipping any interval longer than ``max_gap_s`` (an engine-off gap between
    trips) and any bounded by a ``None`` fuel rate. Each counted interval's time
    is classified moving vs idle by the later sample's speed.

    Args:
        samples: ``(epoch_s, fuel_lph, speed_kph)`` tuples, ascending by time;
            ``fuel_lph``/``speed_kph`` may be ``None``.
        max_gap_s: Longest interval still integrated; longer ones are treated as
            gaps and contribute nothing.
        moving_kph: Speed threshold separating moving from idle time.

    Returns:
        The integrated :class:`DriveSummary`.
    """
    fuel = engine_on = moving = idle = 0.0
    prev_t: float | None = None
    prev_fuel: float | None = None
    for t, fuel_lph, speed in samples:
        if prev_t is not None and prev_fuel is not None and fuel_lph is not None:
            dt = t - prev_t
            if 0.0 < dt <= max_gap_s:
                fuel += (prev_fuel + fuel_lph) / 2.0 * dt / 3600.0
                engine_on += dt
                if (speed or 0.0) > moving_kph:
                    moving += dt
                else:
                    idle += dt
        prev_t = t
        prev_fuel = fuel_lph
    return DriveSummary(fuel_litres=fuel, engine_on_s=engine_on, moving_s=moving, idle_s=idle)
