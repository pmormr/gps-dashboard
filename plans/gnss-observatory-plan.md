# GNSS Observatory plan

Long-term logging and analysis of the satellite constellation the van's receiver
sees — beyond the live `/skyplot`. Accumulate per-satellite observations, map the
van's actual sky (obstruction + signal), and predict satellite passes (rise/set,
appear/disappear) from observed orbital motion.

## Decisions locked

- **Orbit source: fit from observed az/el (route A), not broadcast subframes (route B).**
  gpsd 3.22 emits no `SUBFRAME` by default (probed 2026-06-23: only TPV/SKY/PPS).
  Getting the broadcast almanac would mean reconfiguring the production M9N to
  stream UBX-RXM-SFRBX — a durable receiver-config change on the fragile config
  surface that once stalled logging for days — and it only buys the raw orbital
  elements, since the az/el we'd derive from it is already in every SKY message.
  Route A is zero-hardware-risk, fully offline, and self-contained. Route B stays
  a deliberate later upgrade *if* we ever need ephemeris-grade accuracy or to
  predict never-seen sats.
- **Prediction baseline is sidereal-day repetition.** GPS ground tracks repeat
  every ~23h56m; other constellations on their own known multi-day cycles. The
  cheap, near-mathless predictor is "this SV was at this az/el at this sidereal
  time → it returns ~23h56m later." A proper Keplerian fit is the accuracy upgrade.
- **SKY az/el is the receiver's own computed sat positions** (from its internal
  almanac), so route A's input is a clean angular track, not noisy measurements.

## SKY fields available (probed live)

Each SKY satellite carries: `PRN`, `gnssid`, `svid`, `az`, `el`, `ss` (SNR),
`used`, `health`. ~40 sats in view. We persist only positioned sats (az+el present),
same filter `/api/gpsd/sky` already applies.

## Phases

### Phase 1 — capture (foundation, shared by both goals) — DONE/DEPLOYED
- `sat_observations(timestamp, gnssid, svid, az, el, snr, used, health)` table,
  indexed `(gnssid, svid, timestamp)` + `timestamp`. `timestamp` is canonical
  ms-UTC text like every other tier.
- Logger writes per-SV rows from the SKY array on its own ~60s throttle
  (separate from the 5s `receiver_metadata` throttle; both gate off the same SKY
  message, off the position hot-path). Heartbeat gains a `satobs=` counter.
- Cadence 60s (~50k rows/day, ~18M/yr). One constant; tunable.

### Phase 2 — 3D constellation globe (PC-only) — v1 BUILT
Pivoted from a flat obstruction heatmap to a 3D Earth+constellation viewer (user
call): far easier to read scale, and it folds the signal data in as colour.
- **az/el → 3D**: az/el is a *direction*, not a position, but GNSS sats orbit at
  known nominal altitudes, so the position is recoverable — intersect the
  observer→sat ray with the constellation's orbital sphere. `common/satgeo.py`
  (WGS84 observer ECEF + ray-sphere reconstruction, per-gnssid radii). Approx:
  nominal circular radius only (sub-pixel at whole-Earth zoom; BeiDou/QZSS mix
  orbit classes so those dots are coarse). Reuses no subframes — works offline.
- `GET /api/constellation?start=&end=` reconstructs each `sat_observations` row
  in the window against one representative observer fix, grouped by SV.
- `/globe` page: three.js (vendored, offline), textured Earth in the ECEF frame
  (texture verified aligned — marker lands on the right continent), observer
  marker, per-SV arcs + current dots coloured by constellation, sight-lines,
  window presets, legend toggles. `?demo` synthesises a constellation offline.
- Manual time-window scoping (van moves, so the sky is location-relative; the
  user reads the window knowing when parked). Location/dwell auto-scoping deferred.
- On-the-fly reconstruction, no rollup table yet (revisit when scan time bites).
- **v2 (Phase 3 bridge) — DONE:** full orbit *rings*. `common/satgeo.fit_orbit_normal`
  estimates each SV's orbital-plane normal from its reconstructed arc (sum of
  in-pass consecutive cross products = angular-momentum direction; between-pass
  gaps skipped; None below a min traced arc). The plane passes through Earth's
  centre, so the ring is the great circle at the orbital radius — drawn even
  where we never observed the sat (far side / below horizon). `/api/constellation`
  adds a per-SV `orbit` field; `/globe` draws the ring (toggle). Obstruction/SNR
  heatmap, if still wanted, becomes a separate later view.

### Phase 3 — orbit + prediction — BUILT (awaiting Pi deploy/validation)

All seven items below are implemented, unit/integration tested, and committed
locally. `common/satgeo.py` gained GMST + ECEF↔ECI + `ecef_to_azel`;
`common/orbits.py` is the new fit/propagate/pass-find core; `api/observatory.py`
holds the anchor+reconstruct shared with `/api/constellation`;
`api/routes/passes.py` serves `/api/passes` + `/passes`; `static/js/passes.js` +
`templates/passes.html` render the schedule; `tools/passes_validate.py`
backtests. Remaining: deploy to the Pi and run `passes_validate.py` against real
logged data to confirm the on-sky error is acceptable (synthetic backtest is
0.00°; real data carries eccentricity + nominal-radius + two-body + any
observer-movement error).

**Key insight:** "sidereal-repeat baseline" and "Keplerian fit" aren't separate
methods. Fit and propagate the orbit in an **inertial (ECI) frame** and
sidereal-repeat falls out for free — the orbit is fixed in ECI, the parked
observer rotates with Earth (GMST), so they re-align every sidereal day
automatically, *and* it generalizes to GLONASS/Galileo/BeiDou (whose ground
tracks don't repeat daily) and to arbitrary horizons. So we go straight to ECI.

Cheap because the priors are strong and most geometry already exists:
- **Plane** (inc + RAAN): `fit_orbit_normal` already gives the angular-momentum
  direction — fit it in ECI, not the Earth-rotation-smeared ECEF the rings use.
- **Size** (a → mean motion `n`): fixed per constellation (`orbital_radius_m`);
  not independently observable since reconstruction *assumes* that radius.
- **Phase**: the only real unknown — project the ECI track into the orbital
  plane, fit one epoch phase `θ₀` against the known `n`. 1-DOF per SV.

Side benefit: fitting in ECI also de-smears the Phase-2 orbit rings.

Scope (first cut): two-body propagation, no J2 nodal precession (accurate to
minutes over a 1–2 day horizon; J2 left as a hook). Defaults: trailing fit
window a few days, horizon next 12h, mask elevation 5°. Parked-observer
assumption (anchor to current fix, like `/api/constellation`).

Work items (pure-math core first — high coverage, zero deploy risk):
1. **Frame geometry** — GMST (IAU 1982 polynomial) + ECEF↔ECI Z-rotation in
   `common/satgeo.py`. Unit-tested (known epochs, round-trip).
2. **Orbit fit** — `common/orbits.py`: ECEF samples → ECI → fit plane normal +
   epoch phase vs nominal `n`; reject SVs whose *observed* rate is far off
   nominal (catches BeiDou-GEO/QZSS misclassification). Emit a compact `Orbit`.
3. **Propagator + topocentric az/el** — `Orbit` + t + observer → ECI → ECEF →
   az/el + range (ports `globe.js` `skyAngles` to Python).
4. **Pass finder** — sample el(t) over the horizon, bracket rise/set at the
   mask, bisection-refine, find peak el + az/time.
5. **API** `GET /api/passes?hours=&mask=` — fit all SVs from the trailing
   window, propagate, return the schedule (RINEX name, system, rise/peak/set,
   peak el+az, duration, max SNR). Share the observer-anchor/reconstruct helper
   with `/api/constellation`.
6. **UI** — standalone `/passes` page (phone-friendly "passes coming up"); the
   predicted-arc overlay on `/skyplot` stays Phase 4.
7. **Validation** — backtest tool/test: fit on obs up to T, predict, compare to
   *held-out real* obs az/el; report error distribution (offline confidence with
   no TLE/internet to check against). Plus synthetic unit tests for 2–4.

### Phase 4 — prediction UI
- Predicted arcs overlaid on the skyplot hemisphere; a "passes coming up" schedule.

## Architecture fit

Mirrors the existing capture→derive→serve→render split: the **logger** captures
(already reads SKY), a **processor-like analyzer** does the orbital math, the
**API** serves it, the **skyplot page** renders it. When this lands, fold the
durable bits into a `.claude/modules/` doc and drop this plan.
