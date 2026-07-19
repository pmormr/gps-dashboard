# Processor — denoised / processed tier

`processor/gps_processor.py` is a standalone, enabled-gated service (`gps-processor`)
that tails raw `gps_points` by a persisted id cursor and derives the **processed tier**
the frontend reads — `track_points` + `track_events`. It never writes raw and never
touches gpsd: the logger owns raw, this owns the processed tier. Idempotent and fully
rebuildable from raw (`--rebuild`). The determinism / cursor / provisional contract is
documented in the module docstring at the top of `gps_processor.py` — **read it before
changing the state machine** (output is a pure function of the raw prefix ordered by
`id`; the cursor advances only to finalized emits; an open stop + the open moving
segment stay provisional).

## Why two tiers

Raw `gps_points` is the append-only source of truth (it keeps jittering, so the
logger's receiver-freeze watchdog stays valid); `track_points` is the denoised /
simplified view the map reads. This decouples "source of truth" from "what the map
renders" and solves the max-points problem at the storage level, not on the wire.
Static hold runs **in software on the processed tier**, never on the receiver (see
Eliminated pathways).

## Algorithm — one causal state machine, two regimes

`MOVING | PARKED`, plus a held-position estimator while parked. Each emitted row
carries `n_raw` (raw fixes subsumed) and `importance` (decimation priority); regime
transitions emit `track_events` (`stop_start` / `stop_end`).

**PARKED (software static hold):** enter when speed stays below `stop_speed_enter` for
`stop_min_duration` with displacement within `stop_radius`. While parked, maintain **one
provisional** `kind='stop'` row, continuously recomputing the held position as an
**accuracy-weighted mean** of lat/lon (`w = 1/max(eph, floor)²`, O(1) running weighted
sums — a true geometric median is deferred), down-weighting / rejecting fixes whose
`eph`≈√(epx²+epy²) exceeds `accuracy_reject`, and updating `dwell_end` / `n_raw` /
`radius` as fixes arrive — the estimate improves as the stop lengthens. Exit on
`stop_speed_exit` (hysteresis) or leaving `stop_radius`; finalize, emit `stop_end`,
advance the cursor.

**MOVING (online line simplification, Reumann–Witkam):** extend a segment; emit a
`kind='track'` vertex when a fix deviates perpendicular from the segment by
> `simplify_epsilon`, or on `move_emit_max_gap` (keep-alive on long straightaways). The
triggering deviation becomes the vertex `importance`; straightaways collapse to
endpoints (low count, low importance), sharp turns emit high-importance vertices.
Accuracy gating applies here too.

Net: sparse simplified vertices while driving + one collapsed point per stop, each
weighted so the renderer and the size-aware decimator (`/api/points`) can rank them.

## Tuning knobs

The frozen `Thresholds` dataclass; changing one is the intended trigger for `--rebuild`.
The starting values below were validated against a real drive — no changes needed,
`simplify_epsilon` confirmed at ~2.5 m.

| Knob | Value | Note |
|---|---|---|
| `stop_speed_enter` | 0.5 m/s (~1.1 mph) | Doppler `speed` is the primary stationary signal |
| `stop_speed_exit` | 1.0 m/s | hysteresis so a creep doesn't flap the state |
| `stop_min_duration` | 30 s | low enough to catch a traffic light |
| `stop_radius` | 15 m | release if the solution wanders past this |
| `simplify_epsilon` | ~2.5 m | perpendicular deviation to emit a moving vertex; near raw resolution → lane-level shape at 5 Hz |
| `move_emit_max_gap` | 60 s | keep-alive emit on long straightaways |
| `accuracy_reject` | eph > 50 m | drop / down-weight |

## Traps

- **Live current-position dot reads raw, not the processed tier:**
  `/api/points/latest` serves raw `gps_points`, so a stop row converging over its first
  minute never disturbs the marker; trail/history reads `track_points`.
- **Keep `idx_gps_points_timestamp`:** the processor's own cursor is rowid-based
  and needs no index, but `/api/points/latest`, `/gpsd`, `precache`, and
  `annotations.point_count` still range/order raw *by timestamp* — dropping it
  full-scans a multi-million-row table.
- **Deploy: the processor is enabled-gated:** the post-receive hook installs all
  `deploy/*.service` units on a `deploy/` change (glob — the *install* list isn't
  hardcoded) and restarts `gps-processor` whenever enabled. Note the hook's *restart*
  branches are still per-unit blocks — a brand-new service needs its own restart block
  added to the hook on the Pi (see sensors.md). The one-time Pi-side step is
  `systemctl enable gps-processor` (like `mqtt-ingest`); until then it stays dormant.
  It resumes from its cursor, so restart is always safe.
- **Timestamps are wall-clock `now()` at ms precision:** the Pi clock is chrony
  PPS-disciplined stratum-1, so `now()` *is* GPS-quality time and stays monotonic with
  `id` (the determinism anchor). The processor still clamps negative dwell/`dt` to 0 as
  insurance against a rare chrony step / pre-PPS-lock boot.

## Eliminated / deferred pathways

- **Receiver hardware static-hold** (`CFG-MOT-*`) — rejected: trips the freeze
  watchdog, reverts on power loss (the baud/config saga), lossy at source. The
  automotive dynamic model stays.
- **Accuracy-weighted Kalman over the whole track** — deferred; stop-collapse +
  simplification covers the pain without Kalman tuning overhead. Revisit if residual
  moving jitter matters.
- **Smooth-zoom LOD (Visvalingam effective-area)** — deferred; the decimator ranks by the
  single-level RW deviation. A batch Visvalingam pass would give nested multi-level
  importance for pop-free zoom; swappable for free later (idempotent → just a rebuild).
- **Retroactive cleanup of historical raw** — out of scope; raw is preserved so a
  rebuild is always possible.
- **RTK sub-meter** — out of scope (needs a correction stream + base/NTRIP; off-grid
  incompatible). ~1–2 m hAcc is the M9N floor; denser sampling sharpens *relative* track
  shape only, not absolute accuracy.
