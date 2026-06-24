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

### Phase 3 — orbit + prediction
- Per-SV orbit model from accumulated az/el: start with the sidereal-repeat
  baseline, upgrade to a Keplerian fit (strong priors — near-circular, known
  semi-major axis / period per constellation).
- Propagator → az/el(t); rise/set + pass windows. Processor-like analyzer
  component (mirrors `gps-processor`), API serves the predictions.

### Phase 4 — prediction UI
- Predicted arcs overlaid on the skyplot hemisphere; a "passes coming up" schedule.

## Architecture fit

Mirrors the existing capture→derive→serve→render split: the **logger** captures
(already reads SKY), a **processor-like analyzer** does the orbital math, the
**API** serves it, the **skyplot page** renders it. When this lands, fold the
durable bits into a `.claude/modules/` doc and drop this plan.
