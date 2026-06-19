# GPS Denoise & Two-Tier Storage Plan

> Living plan. Check items off as they land, record decisions inline. Markup
> welcome — leave comments against any row and we'll resolve them before
> writing code.
>
> **Iteration 3** — locked P2/P3/P4/P7/P8/P9 (→ C12–C18); moving-regime thinning
> reframed as online line simplification (C19); added point "size"/importance
> metadata (C14), a processor `track_events` table (C15), size-aware decimation
> (C17), and the 5 Hz storage footprint. New open decision: motion-gated raw
> write rate (P10).
>
> **Iteration 4** — locked P10 as tiered motion-gated writes (C20); ms-precision
> canonical timestamps across all tiers (no raw divergence); clarified the
> RW-vs-Visvalingam importance metric for LOD.

## Context

Today `gps_points` does double duty: it is both the **raw append-only stream**
and the **source the frontend reads**. The logger throttles writes to one point
per 5s, so a parked hour is ~720 rows — and the van is parked ~95% of the time.
The result:

- **The frontend hits max-points limits** loading windows that are mostly dwell.
- **Stationary jitter is visible** even on short stops. A live GPS fix wanders a
  few meters even when the receiver isn't moving (the logger's freeze watchdog
  actually *depends* on this — `FROZEN_POSITION_SECONDS`). The Automotive dynamic
  model is already set on the M9N; we are **not** enabling the receiver's
  hardware static-hold (it would trip that watchdog and is fragile across power
  loss — see the rejected option below).

The fix is structural: **split raw capture from the data that drives the
frontend**, and put a **processor** between them that strips redundant/irrelevant
points and denoises stops — *not* blind time-decimation. A parked hour collapses
to ~1 meaningful point; a drive keeps only points that carry information (genuine
bends and stops), each tagged with how much data it represents.

This decoupling lets us **raise raw to 5 Hz** (C18) for lane-level moving fidelity
without ever touching the frontend's point budget, because the frontend reads the
processed tier.

We are still prototyping. **Historical/retroactive cleanup is a non-goal** — the
current data is "good enough," and keeping raw intact means we can rebuild the
processed tier from scratch whenever we retune.

---

## Confirmed decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| C1 | Static hold | **Implement in software, on the processed tier** | Raw keeps jittering, so the logger's receiver-freeze watchdog stays valid. The clamped-parked-position benefit without the hardware config's persistence fragility. |
| C2 | Storage shape | **Two tiers: raw `gps_points` + processed `track_points`** | Decouples "source of truth" from "what the map reads." Solves max-points at the storage level, not just the wire. |
| C3 | Processor role | **Strip redundancy + denoise, not decimate** | Drop points that track nothing meaningful, not thin uniformly by time. |
| C4 | Schema | **Extend raw with per-fix accuracy fields** | Unlocks accuracy-weighted denoise now and richer analysis later. |
| C5 | Retroactive | **Out of scope** | Prototyping; raw is preserved so a rebuild is always possible if that changes. |
| C6 | Receiver static-hold | **Rejected** | Trips the freeze watchdog; reverts on power loss (documented hardware saga); lossy at source. Automotive dynamic model stays. |
| C7 | Processor determinism | **Deterministic & idempotent — fully re-runnable from raw** | Same source prefix → identical output (`track_points` *and* `track_events`). Fearless rebuilds when retuning. See *Determinism & idempotency*. |
| C8 | Raw cadence (base) | **Log raw at the native nav rate** | Superseded by C18 (5 Hz). Keeps every fix the receiver produces, motion-gating aside (C20). |
| C9 | Receiver telemetry | **SKY data → separate `receiver_metadata` table (~5s throttle)** | SKY is a different message/cadence; keeping it off the position hot-path decouples concerns. The denoiser uses per-fix `epx`/`epy`, not DOP, so this table is pure future-use telemetry. |
| C10 | Stop hold | **Refine continuously over the dwell; finalize on close** | The held position is a robust estimate recomputed over all accumulated fixes, improving as the stop lengthens — not written once on entry. |
| C11 | Where processing runs | **New `gps-processor` systemd service** tailing raw by id cursor | Keeps the logger dumb and unkillable (sole writer of raw). Processor can crash/restart/rebuild with zero risk to capture. |
| C12 | Processing model | **Incremental / online** (a data-pipeline processor: tail → transform → emit) | Keeps the live trail fresh; the filter is causal. Batch would lag live. |
| C13 | Live "current position" | **`/api/points/latest` reads raw**; trail/history reads `track_points` | Real-time dot *and* clean history. The live dot reads raw, so a stop row converging over its first minute never disturbs the marker. |
| C14 | Point "size" metadata | **Every `track_point` carries `n_raw` (fixes that went into it) + an `importance` score**; stops also carry dwell | Answers "how much data is behind this point." Drives both the rendered dot size and size-aware decimation (C17). For stops the two coincide (big + protected); for moving vertices, `importance` is the simplification significance (C19). |
| C15 | Auto-events | **Processor emits "interesting" events to a separate `track_events` table** | Mode transitions, rate-of-change spikes, average drift, stop start/end. Distinct from the user-curated `annotations` table; can be promoted to annotations later. Rebuildable like `track_points`. |
| C16 | Naming | `track_points` / `receiver_metadata` / `processing_state` / `track_events` | Bikeshed-friendly; settled. |
| C17 | Large-range decimation | **`?bucket=` retained but made size-aware** — rank by `importance`, drop lowest first; stops & sharp turns protected | A year-scale view must still thin, but not blindly: a huge dwell point or a sharp turn must survive while a 3-hour straightaway collapses. Filter `kind='stop' OR importance >= floor(span)`. |
| C18 | Raw nav rate | **5 Hz via UBX-NAV-PVT, baud 38400** | Lane-level moving fidelity (~2.7 m/vertex at 30 mph). UBX's compactness keeps it within the baud budget so the baud doctrine is untouched; a rate-revert is graceful. See *Raw spatial resolution*. |
| C19 | Moving-regime thinning | **Online line simplification (Reumann–Witkam / open-window)** | Emit a vertex on perpendicular deviation > `simplify_epsilon`, not on raw distance — so straightaways collapse to endpoints and vertices land at genuine bends. The deviation magnitude is the per-vertex `importance` (C14). |
| C20 | Raw write cadence | **Tiered motion-gated writes** — 5 Hz moving, throttle to ~1 Hz (or 0.2 Hz) parked; room for more tiers keyed on speed/dynamics | 5 Hz parked is pure correlated bloat; the 5 Hz goal is moving sharpness. Cuts raw ~4–8× (~19→~5 GB/yr). One speed conditional on the existing write throttle; the freeze watchdog (tracks the fix *stream*, not writes) is unaffected. Cost: raw is no longer "every native fix" — a small, accepted purity hit. |

---

## Open decisions

All resolved. *(Iteration history: P1→C11, P5→C9, P6→C8; P2→C12, P3→C13, P4→C14,
P7→C16, P8→C17, P9→C18, P10→C20.)*

---

## Constraints carried from the project

- **GPS logging is sacred.** `logger/gps_logger.py` stays minimal and remains the
  only writer of raw `gps_points` (and now `receiver_metadata`). The processor
  never touches gpsd and never blocks the logger. Logger changes are limited to:
  5 Hz position writes (motion-gated per P10), the new TPV accuracy columns, and a
  SKY branch writing `receiver_metadata` on its own throttle.
- **Offline-first.** Pure local compute; no new runtime deps without asking. The
  processor is stdlib + sqlite3.
- **Same DB.** All tiers live in `/mnt/nvme/data/gps_history.db` (persists across
  deploys) so processed↔raw and GPS↔sensor stay local joins.
- **Deploy model.** New `deploy/gps-processor.service` slots into the post-receive
  hook (always-restart, like `gps-dashboard`/`mqtt-ingest` — it resumes from its
  cursor, so a restart is safe). CLAUDE.md deploy section updated when it lands.
- **Reuse what exists.** Migrations go through `api.db.migrate`. All tiers stay on
  `api.db.canonical_timestamp`, **extended to millisecond precision** (fixed-width,
  still lexically sortable) so raw 5 Hz fixes are sub-second-distinct without
  fragmenting the timestamp story — the function's job is tz/format normalization
  and lexical alignment, not cadence (see Phase 1 note). The processor follows the
  logger ethos: heartbeat with dropped/emitted counters, graceful shutdown,
  `KeyboardInterrupt` → exit 130 if run as a tool.

---

## Architecture

```
gpsd ──> gps-logger ──┬─INSERT (5Hz, motion-gated)──> gps_points (raw, +accuracy cols)
                      └─INSERT (~5s)───────────────-─> receiver_metadata (DOP, sat counts)
                                     │
                          tail by id cursor (WAL reader)
                                     ▼
                               gps-processor  ── online filter ──┬──> track_points (processed)
                                     │                           └──> track_events (interesting events)
                                     │
api /api/points/latest ──────reads raw──────────────────────┐
api /api/points (trail/history) ──reads processed───────────┴──> frontend
```

- **Writer invariant preserved:** logger → raw (`gps_points` + `receiver_metadata`);
  processor → processed (`track_points` + `track_events`) only.
- **Concurrency:** SQLite WAL handles one writer + concurrent readers cleanly.
- **Rebuildable:** drop `track_points`/`track_events`, reset the cursor, restart →
  re-derives everything from raw. The prototyping superpower (C7).

---

## Determinism & idempotency (C7)

The processor must produce **bit-identical** output for a given raw prefix, so a
rebuild after a retune is trustworthy.

- **Deterministic algorithm.** State transitions depend only on raw rows (ordered
  by `id`, which equals time order on an append-only table) and the fixed
  threshold config. No wall-clock, no RNG. The robust stop estimate and the
  line-simplification significance are *recompute-from-fixes* (same fixes → same
  result), never an order-dependent incremental accumulator.
- **Commit at safe boundaries.** The persisted cursor `last_committed_raw_id`
  advances only to the last *finalized* emit. An open stop, an open moving
  segment, and any un-emitted tail are **provisional**.
- **Restart.** Load the cursor, delete provisional `track_points`/`track_events`
  (`src_raw_id` beyond the cursor), reprocess forward → identical output.
  Replaying even a multi-hour tail is sub-second.
- **Full rebuild.** Truncate `track_points` + `track_events`, set cursor → 0, run.
- **Caveat:** idempotency holds *per threshold set*. Changing a threshold is the
  intended trigger to rebuild — a feature, not a violation.
- **Caveat (vs. code):** Phase 1 sources the raw `timestamp` from the TPV `time`
  field (GPS time) while `id` is insertion order. These can disagree — GPS time can
  jitter backwards a little, and the logger's only time guard rejects fixes >10 s
  *behind* wall clock (`GPS_TIME_MAX_AGE_SECONDS`, `logger/gps_logger.py:15`), not
  small reorderings. So "`id` == time order" is *almost* true but not guaranteed;
  the processor's dwell/`dt` math must clamp negative intervals to 0 to stay
  deterministic. Also specify the fallback when TPV `time` is absent (a fix can
  arrive without it) — the logger currently always uses wall-clock `now()`
  (`logger/gps_logger.py:234`); switching the source needs a defined fallback.
- **Open-stop cost:** while the van is parked for days, no boundary finalizes, so the
  cursor never advances and a restart reprocesses the whole open dwell. At 1 Hz
  parked that is ~86 k rows/day — still fast, but the "replaying a multi-hour tail is
  sub-second" claim should say *bounded by the longest open stop*, not the tail.

---

## The processed-tier algorithm (software static hold + online simplification)

One causal state machine over the raw stream, two regimes. Each emitted row gets
`n_raw` (fixes subsumed) and `importance` (decimation priority); regime
transitions also emit `track_events`.

**State:** `MOVING` | `PARKED`, plus a held-position estimator while parked.

**PARKED (software static hold):**
- Enter when speed stays below `stop_speed_enter` for `stop_min_duration`
  (confirmed by displacement staying within `stop_radius`). Emit a `stop_start`
  event.
- While parked, accumulate the stop's fixes and **recompute** a robust,
  accuracy-weighted position estimate (weighted/geometric median of lat/lon;
  down-weight or drop fixes whose `eph`≈√(epx²+epy²) exceeds `accuracy_reject`).
  Maintain **one provisional** `track_points` row (`kind='stop'`), updating
  `lat/lon`, `dwell_end`, `n_raw`, `radius` as fixes arrive (C10). `importance`
  is high (function of dwell) → protected from decimation.
- Exit when speed exceeds `stop_speed_exit` (hysteresis) or displacement leaves
  `stop_radius`. Finalize the row, emit a `stop_end` event, advance the cursor.

**MOVING (online line simplification — C19):**
- Extend a segment in the current direction; emit a `kind='track'` vertex when a
  fix deviates perpendicular from the segment by > `simplify_epsilon`, or on
  `move_emit_max_gap` (keep-alive on long straightaways).
- The deviation that triggered the emit becomes the vertex's `importance`; `n_raw`
  = fixes since the last vertex. Straightaways collapse to endpoints (low point
  count, low importance); sharp turns emit high-importance vertices.
- Accuracy gating applies here too (drop/down-weight low-quality fixes).
- Rate-of-change / average-drift heuristics can emit `track_events` here too.

Net output: sparse simplified vertices while driving + one collapsed point per
stop, each weighted so the renderer and the size-aware decimator (C17) can rank
them.

### Tuning knobs (starting points — tune against the Chick-fil-A trip)

| Knob | Start | Note |
|------|-------|------|
| `stop_speed_enter` | 0.5 m/s (~1.1 mph) | Doppler `speed` is the primary stationary signal |
| `stop_speed_exit` | 1.0 m/s | Hysteresis so a creep doesn't flap the state |
| `stop_min_duration` | 30 s | Low enough to catch a traffic light; raise if slow rolls collapse |
| `stop_radius` | 15 m | Release if the solution wanders past this |
| `simplify_epsilon` | 2–3 m | Perpendicular deviation to emit a moving vertex (RW). Set near the raw resolution; at 5 Hz this delivers lane-level shape |
| `move_emit_max_gap` | 60 s | Keep-alive emit on long straightaways |
| `accuracy_reject` | eph > 50 m | Drop or down-weight |
| `parked_write_hz` (logger, P10) | ~1 Hz | Raw write rate while stationary if motion-gated |

---

## Raw spatial resolution (C18)

At 1 Hz, moving vertex spacing = speed × 1s (~13 m at 30 mph). Finer spacing comes
only from a **higher nav rate**; the constraint is the serial link, and the
receiver's ~1–2 m accuracy is the floor we're *sampling*, not something we can beat.

- **Lever:** raise the M9N nav rate. 5 Hz → ~2.7 m/vertex, 10 Hz → ~1.3 m at 30 mph.
- **Throughput:** full NMEA at 38400 baud caps ~2–4 Hz. Switch position output to
  **UBX-NAV-PVT** (~108 B/epoch; carries lat/lon + hAcc/vAcc + velocity): 5 Hz ≈
  540 B/s, 10 Hz ≈ 1.1 KB/s — both fit 38400 (~3.8 KB/s) with room. gpsd parses
  UBX natively and still fills TPV (`epx`/`epy` from hAcc).
- **Keep baud at 38400.** UBX's compactness means we do *not* touch baud — the
  documented stall risk (gpsd forcing a baud the module reverted away from) stays
  avoided. And unlike baud, a nav-rate/protocol revert on power loss is
  **graceful**: gpsd auto-detects NMEA 1Hz and keeps logging. Persist to flash anyway.
- **Accuracy floor:** ~1–2 m hAcc is the M9N's real autonomous/SBAS precision.
  Denser sampling sharpens *relative* track shape (common-mode errors cancel
  between adjacent fixes) but does not lower per-fix absolute accuracy. True
  sub-meter = RTK (different receiver + correction stream + base/NTRIP) — off-grid
  incompatible, out of scope.
- **PPS/NTP unaffected** — the 1Hz timepulse is independent of nav rate.

Receiver-config sub-task (ubxtool: `CFG-RATE-MEAS`/`CFG-RATE-NAV` +
`CFG-MSGOUT-UBX_NAV_PVT_UART1`, persist to flash), sequenced alongside Phase 1.
Target 5 Hz; test 10 Hz empirically.

> **Message selection (open).** The baud budget above assumes UBX-NAV-PVT *only*.
> The default NMEA sentences must be **disabled on UART1** (`CFG-MSGOUT-NMEA_*_UART1
> = 0`), or NMEA (esp. GSV across 4 constellations at 5 Hz) competes for the link and
> blows the budget. But the SKY-sourced `receiver_metadata` (C9) depends on what gpsd
> can derive: NAV-PVT carries `pDOP` and `numSV` only — so `pdop`/`nsat_used` are
> fillable, but `hdop`/`vdop`/`nsat_seen` need **NAV-DOP** and **NAV-SAT** enabled
> too (NAV-SAT is per-satellite and not free at 5 Hz). Decide whether to throttle the
> DOP/SAT messages (they only need ~5 s cadence, not the nav rate) or accept partial
> `receiver_metadata`. Also verify gpsd emits a `SKY` class at all under UBX-only —
> the logger's new SKY branch is a no-op otherwise.

---

## Storage footprint (5 Hz)

Raw row ≈ ~135 B in-table (10 REAL accuracy/position fields + int mode + a
fixed-width ms timestamp string). The processor tails raw by its rowid cursor, so
*it* needs no timestamp index. (ms-text costs ~15 B/row over an integer epoch; with
motion-gating that delta is sub-GB/yr — consistency wins.)

> **Index caveat (vs. code).** "No timestamp index needed" only holds for the
> processor. Other readers still range/order raw `gps_points` *by timestamp* and
> would regress to full scans without `idx_gps_points_timestamp` (`api/db.py:57`):
> `/api/points/latest` orders by `timestamp DESC` (`api/routes/points.py:16`), the
> `/gpsd` page reads the latest fix and the frozen-window (`api/routes/status_gpsd.py:79,106`),
> `precache.py:131` reads the latest fix, and — even after the frontend trail moves
> to `track_points` — the annotations list still counts raw points inside each range
> (`api/routes/annotations.py:43`). Keep the index, or migrate these readers to rowid
> order first. At 38 M+ raw rows/yr this is the difference between a fast page and a
> table scan.

| Raw write policy | Rows/year | GB/year |
|---|---|---|
| 5 Hz always (24/7, continuous fix) | ~158 M | ~19–25 |
| Motion-gated, 1 Hz parked (P10) | ~38 M | ~5–6 |
| Motion-gated, 0.2 Hz parked | ~14 M | ~2–3 |

All far under 100 GB/yr. `receiver_metadata` at ~5s ≈ 6 M rows/yr (negligible).
`track_points`/`track_events` are sparse (orders of magnitude smaller). Raw
retention/rollup stays deferred but becomes more relevant if we run 5 Hz-always
for years.

---

## Schema

### Raw — extend `gps_points` (additive migration; existing rows get NULLs)

| Column | Type | Source | Why |
|--------|------|--------|-----|
| `epx` | REAL | TPV | Longitude error (m, 1σ) — horizontal accuracy |
| `epy` | REAL | TPV | Latitude error (m, 1σ) |
| `epv` | REAL | TPV | Altitude error (m) |
| `eps` | REAL | TPV | Speed error (m/s) |
| `climb` | REAL | TPV | Vertical velocity (m/s) |
| `mode` | INTEGER | TPV | Fix mode (2=2D, 3=3D) — quality |

At 5 Hz the `timestamp` carries sub-second precision via the ms-extended
`canonical_timestamp` (e.g. `2026-06-09T14:55:55.200Z`) — fixed-width and lexically
sortable, shared by all tiers (no divergence).

### Raw telemetry — new `receiver_metadata` (~5s throttle, SKY-sourced)

| Column | Type | Note |
|--------|------|------|
| `id` | INTEGER PK | |
| `timestamp` | TEXT | UTC, ms canonical (uniform across tiers) |
| `hdop` | REAL | Horizontal dilution of precision |
| `vdop` | REAL | Vertical DOP |
| `pdop` | REAL | Position DOP |
| `nsat_used` | INTEGER | Satellites used in solution |
| `nsat_seen` | INTEGER | Satellites in view |

Standalone telemetry — not joined into the position path. Extend later
(per-constellation counts, gdop/tdop) if a use emerges.

### Processed — new `track_points`

| Column | Type | Note |
|--------|------|------|
| `id` | INTEGER PK | |
| `timestamp` | TEXT | Representative time (stop: entry or midpoint) |
| `lat`, `lon` | REAL | Denoised position (stop: refined held estimate) |
| `speed`, `altitude`, `track` | REAL | Representative at emit |
| `kind` | TEXT | `'track'` \| `'stop'` |
| `n_raw` | INTEGER | Raw fixes that went into this point — the "size" (C14) |
| `importance` | REAL | Decimation priority (C14): stop dwell weight, or moving-vertex deviation (C19) |
| `accuracy` | REAL | Representative eph |
| `dwell_start`, `dwell_end` | TEXT | Stops only |
| `radius` | REAL | Stops only — spatial spread of the dwell |
| `src_raw_id` | INTEGER | Last contributing raw `gps_points.id` (provenance + provisional cleanup) |

### Processed events — new `track_events`

| Column | Type | Note |
|--------|------|------|
| `id` | INTEGER PK | |
| `timestamp` | TEXT | Event time (or start) |
| `end_time` | TEXT | Nullable — for ranged events |
| `type` | TEXT | `stop_start` \| `stop_end` \| `mode_transition` \| `rate_spike` \| `drift` \| … |
| `magnitude` | REAL | Optional — strength/score of the event |
| `payload` | TEXT | Optional JSON for type-specific detail |
| `src_raw_id` | INTEGER | Provenance + provisional cleanup |

Processor output, rebuildable like `track_points`. Distinct from the user-curated
`annotations` table; a UI can surface these as suggestions and promote them.

### Cursor — new `processing_state`

| Column | Type | Note |
|--------|------|------|
| `key` | TEXT PK | e.g. `last_committed_raw_id` |
| `value` | TEXT | |

---

## API & frontend impact

- `/api/points` (trail/history) reads `track_points`, returning `n_raw`/`importance`
  so the renderer can size dots and the client/serverside decimator can rank.
- `/api/points/latest` keeps reading raw `gps_points` (live dot = true current fix).
- `?bucket=` reworked to **size-aware** decimation (C17): for huge spans, filter
  `kind='stop' OR importance >= floor(span)` rather than grouping blindly by time.
  > **Scale caveat (open, vs. C14/C19).** `importance` is *not* one comparable scale
  > today: a stop's importance is a dwell weight (seconds/log-seconds), a moving
  > vertex's is a perpendicular deviation (metres). A single threshold
  > `importance >= floor(span)` can't rank across both kinds, and `floor(span)`
  > mixes a time-span with deviation-metres dimensionally. Normalize importance to a
  > common 0–1 rank at emit time, or decimate per-kind. Also: the current handler
  > bounds output with `ORDER BY timestamp ASC LIMIT ?` and reports `truncated` when
  > `len == limit` (`api/routes/points.py:79,94`); an importance filter alone is
  > unbounded on dense data, so it still needs `ORDER BY importance DESC LIMIT` then a
  > re-sort by time for rendering — two-stage, and the `truncated` semantics change.
- **The annotations list still range-queries raw** (`api/routes/annotations.py:43`):
  `point_count` is `COUNT(*)` over `gps_points WHERE timestamp BETWEEN start AND end`.
  Moving the trail to `track_points` does *not* move this. Decide: keep it on raw
  (needs `idx_gps_points_timestamp`; O(range) per annotation over a 38 M-row table on
  a page that lists *every* annotation), or re-point it at `track_points`/`n_raw`
  (cheaper, but the count then means "processed points," a semantic change).
- A future `/api/events` (or inclusion in the points payload) surfaces `track_events`.
- Frontend trail rendering still consumes a point list; stop rows / high-`n_raw`
  points can get a distinct marker (dwell pin) sized by `n_raw`.

---

## Deploy & ops impact

- New `deploy/gps-processor.service` (enabled-gated restart in the post-receive
  hook, matching `mqtt-ingest` — CLAUDE.md: the hook always restarts
  `gps-dashboard` and restarts `mqtt-ingest` *if enabled*; mirror that so a host
  without the processor enabled never crash-loops).
  > **Hook edit (vs. docs).** The post-receive hook lives on the **Pi bare repo**
  > (`/mnt/nvme/gps-dashboard.git`), **not in this repo**, and per CLAUDE.md it
  > hardcodes reinstalling *"all four unit files"*. Committing
  > `deploy/gps-processor.service` here does **not** teach the hook about a fifth
  > unit — adding install + restart for it is a manual Pi-side hook edit. Call this
  > out as an explicit deploy step; the unit also needs `PYTHONPATH` + `GPS_DB_PATH`
  > env like the other units (it imports `api.db`).
- Restart is safe at any time — the processor resumes from `processing_state` and
  recomputes the provisional tail (C7).
- Heartbeat to the journal: emitted track points, emitted/updated stops, events
  emitted, raw rows consumed, dropped-by-accuracy count.
- CLAUDE.md (Processes, Data Model, Project Structure, Deploy) updated when it lands.

---

## Phasing / action items

- [ ] **Phase 0 — Receiver config.** Set 5 Hz + UBX-NAV-PVT on the M9N (ubxtool),
      persist to flash, confirm gpsd reports 5 Hz TPV with `epx`/`epy`. Decouple-able
      from the rest (graceful fallback to 1 Hz).
- [ ] **Phase 1 — Schema + logger.** Add `gps_points` TPV columns, `track_points`,
      `track_events`, `receiver_metadata`, `processing_state`. New *tables* go in
      `init_db`'s `CREATE TABLE IF NOT EXISTS` block (`api/db.py:47`); new *columns*
      on the existing `gps_points` go through `_add_missing_columns` in
      `api.db.migrate` (`api/db.py:162`) — the Pi's `gps_points` already exists, so
      `CREATE TABLE IF NOT EXISTS` alone won't add them.
      Logger: write raw at 5 Hz (motion-gated per P10), **fixed-width ms-text**
      timestamp via the ms-extended `canonical_timestamp` (not an integer epoch —
      see C20 and the storage note: the decision is ms-text, uniform across tiers),
      populate TPV accuracy fields, and add a SKY branch writing `receiver_metadata`
      on a ~5s throttle.
- [ ] **Phase 2 — Processor skeleton.** `gps-processor` service: tail raw by the
      committed cursor, **copy-through** to `track_points` (no denoise yet) to
      validate plumbing, cursor persistence, WAL concurrency, deploy — and prove
      idempotency (run twice, diff output).
- [ ] **Phase 3 — Online filter.** Implement the parked/moving state machine
      (static hold with continuous refinement + online line simplification +
      accuracy gating), `n_raw`/`importance` tagging, `track_events` emission,
      safe-boundary commits.
- [ ] **Phase 4 — Wire the frontend.** Point `/api/points` at `track_points`;
      keep `/api/points/latest` on raw; rework `?bucket=` to size-aware; confirm
      live + history both behave.
- [ ] **Phase 5 — Tune.** Calibrate the knobs against the marked Chick-fil-A trip
      and a parked-overnight window; eyeball before/after.
- [ ] **Phase 6 (future) — Events in the UI.** Surface `track_events` as suggestions;
      optionally promote to `annotations`.

### Phase 1 note — timestamps & dedup at 5 Hz

`canonical_timestamp` currently emits whole-second UTC; at 5 Hz that collides and
gives no sub-second dedup key. Extend it to **fixed-width ms**
(`%Y-%m-%dT%H:%M:%S.%fZ`, 3-digit fraction) and source raw time from the TPV `time`
field, deduping on the ms string. Keep the format **uniform across tiers** so
lexical range comparisons stay correct — mixing widths breaks ordering (`'.'` sorts
before `'Z'`), so a one-time migration rewrites existing whole-second rows to
`.000Z`. Cheap in a prototype; flag for review before implementing.

> **Writer-site audit (vs. code).** Extending `canonical_timestamp` alone is *not*
> enough: several writers build the whole-second string directly with
> `strftime('%Y-%m-%dT%H:%M:%SZ')` and bypass the function. Each must move to ms in
> the same change, or it re-introduces the width mismatch and the `'.'`<`'Z'` hazard:
> the logger INSERT (`logger/gps_logger.py:234`), the `marks` upsert
> (`api/routes/annotations.py:162`), and the frozen-window cutoff the `/gpsd` page
> compares against raw (`api/routes/status_gpsd.py:103` — a whole-second cutoff vs.
> ms-stored rows drops the cutoff-second's points, since `.000Z` < `Z`).
>
> **Migration cost (vs. code).** The one-time rewrite of existing rows is a
> full-table `UPDATE` on `gps_points` (already 1 yr+ of 5 s data, growing to 38 M+/yr
> at 5 Hz). Guard it idempotently (e.g. `WHERE timestamp NOT LIKE '%.%'`) and note it
> runs inside `migrate()` (`api/db.py:186`), which executes at **both** logger and
> app startup — a heavy `UPDATE` there briefly holds the WAL write lock against the
> live logger (`busy_timeout=30000`, `api/db.py:41`). The browser already sends
> ms-precision bounds via `toISOString()` (`static/js/timeline.js:74`), so today's
> params↔storage width mismatch is *latent*; the ms storage migration actually makes
> the comparison fully consistent.

---

## Rejected / deferred

- **Receiver hardware static-hold** (`CFG-MOT-*`): rejected (C6). Automotive
  dynamic model stays.
- **Accuracy-weighted Kalman over the whole track:** deferred. The stop-collapse +
  simplification combo addresses the stated pain without Kalman tuning overhead;
  revisit if residual moving-jitter matters once accuracy is logged.
- **Smooth zoom LOD (Visvalingam):** deferred. C17 ranks by `importance`; we start
  with the Reumann–Witkam deviation (single-level, computed online). A batch
  Visvalingam **effective-area** pass over finalized segments would give a *nested*
  multi-level importance for pop-free zooming (the mapshaper/TopoJSON model).
  Swappable for free later — the processor is idempotent (C7), so changing the
  metric is just a rebuild, no migration. Adopt if zoom ever feels poppy.
- **Retroactive cleanup of historical data:** out of scope (C5).
- **Raw retention / rollup policy:** deferred. At 5 Hz-always raw grows ~20 GB/yr;
  revisit a downsample-old-raw policy if NVMe pressure appears (or adopt P10).
- **Satellite skyplot / per-satellite capture:** future. gpsd's SKY message already
  carries per-satellite elevation, azimuth, SNR, used-flag, and constellation
  (4-constellation on the M9N) — everything for a polar skyplot. A *live* skyplot
  needs no schema (read gpsd's SKY directly, like `/gpsd`). *Historical* replay
  would add a `satellite_observations` child table keyed to each SKY epoch —
  deferred as voluminous (~100M+ rows/year) and speculative. `receiver_metadata`
  stays summary-only (DOP + sat counts) for now; the logger already parses SKY, so
  adding per-sat capture later is cheap.
```
