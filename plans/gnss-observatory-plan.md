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

### Phase 1 — capture (foundation, shared by both goals) — IN PROGRESS
- `sat_observations(timestamp, gnssid, svid, az, el, snr, used, health)` table,
  indexed `(gnssid, svid, timestamp)` + `timestamp`. `timestamp` is canonical
  ms-UTC text like every other tier.
- Logger writes per-SV rows from the SKY array on its own ~60s throttle
  (separate from the 5s `receiver_metadata` throttle; both gate off the same SKY
  message, off the position hot-path). Heartbeat gains a `satobs=` counter.
- Cadence 60s (~50k rows/day, ~18M/yr). One constant; tunable.

### Phase 2 — sky / signal map (no orbital math)
- Roll raw observations into an az/el grid (≈1°×1° bins: count + SNR stats);
  let raw rows age out once binned. Decide retention here, with real growth seen.
- API endpoint + view: obstruction map (where the van loses sky) + SNR-vs-az/el
  heatmap. Likely an extension of `/skyplot` or a sibling page.

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
