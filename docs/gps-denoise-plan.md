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
>
> **Iteration 5** — folded the codebase review (PR #1) into settled decisions:
> kept the raw timestamp index (C21); reworked C17 size-aware decimation to
> per-kind (stops always survive, moving vertices ranked within-kind); pinned the
> UBX message set incl. NMEA-off-UART1 + throttled DOP/SAT (C22); kept wall-clock
> `now()` as the timestamp source (it's PPS-disciplined stratum-1) and extended it to
> ms, with a defensive negative-clamp (C23); enabled-gated the processor unit and
> flagged the manual Pi-side hook edit (C24). The Phase-1 writer-site and
> ms-migration audits are captured as implementation notes, not open decisions.
>
> **Iteration 6** — landed Phase 1 in code (ms timestamps + processed-tier schema
> + logger accuracy/motion-gating/SKY) and executed Phase 0 on the M9N. Phase 0
> reality vs C22: gpsd 3.22 already runs the receiver in **UBX binary** mode with
> NMEA disabled in flash and NAV-PVT/SAT/DOP enabled, and emits a **fully populated
> SKY** (hdop/vdop/pdop, nSat=40, uSat=23) — so the C22 SKY-fallback and NMEA-off
> steps were already satisfied. The one durable change made was `CFG-RATE-MEAS=200`
> (5 Hz), persisted to flash; the factory default stays 1 Hz, so a flash wipe
> gracefully reverts. NAV-SAT/DOP were **not** throttled to ~5 s: the 38400 link
> empirically sustains 5 Hz TPV+SKY at 40 sats with zero packet errors, and gpsd
> re-enables its own message set at rate 1 on every device activation, so a flash
> throttle wouldn't survive a gpsd restart anyway. SKY is throttled where it
> matters — in the logger's `receiver_metadata` write (~5 s), not on the wire.
>
> **Iteration 7** — Phase 1 + Phase 2 deployed to the Pi. The logger now writes
> motion-gated 5 Hz/1 Hz with accuracy cols + `receiver_metadata`; `gps-processor`
> is enabled and live as a **copy-through skeleton** (no denoise yet), backfilled
> ~269 k `track_points` and tails raw with backlog 0. The post-receive hook was
> taught the 5th unit (install + enabled-gated restart). Next up is Phase 3 (the
> online filter) — nothing reads `track_points` yet, so the skeleton is inert from
> the frontend's view; Phase 4 wires it in.
>
> **Iteration 8** — landed Phase 3 in code: the copy-through `process_batch` is
> replaced by a causal `TrackFilter` (states MOVING / CANDIDATE / PARKED). Stops
> use an **accuracy-weighted mean** with O(1) running accumulators + eph rejection
> rather than C10's geometric median — bounded memory on a multi-day open stop and
> a free swap later (C7); flagged and confirmed. Moving uses Reumann–Witkam, with
> `importance` = the breaking perpendicular deviation and a `move_emit_max_gap`
> keep-alive. The committed cursor advances only to the last finalized emit; the
> open dwell + its `stop_start` are written as a provisional snapshot and re-derived
> on restart. Determinism is enforced by reconstructing the moving anchor from the
> last committed vertex on startup, and verified with a synthetic harness: a
> mid-stop restart and a double rebuild both reproduce byte-identical
> `track_points`/`track_events`. **Not yet wired to the frontend (Phase 4), and the
> knobs are untuned (Phase 5 — calibrate against the Chick-fil-A trip).**
>
> **Iteration 9** — landed Phase 4: `/api/points` now reads the processed tier
> (`track_points`) instead of raw, with server-side **size-aware decimation** (C17)
> — stops always kept, the `limit` budget filled with the highest-`importance`
> moving vertices, re-sorted by time — replacing the client's `?bucket=`
> time-bucketing (`Timeline.bucketFor` deleted). `/api/points/latest` stays on raw
> (the live dot tracks the true current fix, C13). Verification against a Pi DB
> snapshot caught one real bug: **stops must match a window by dwell-interval
> overlap**, not their representative timestamp, or a long open dwell drops out of
> any recent window while the van is still parked there — fixed. One presentation
> nuance flagged (not blocking): because an overlapping lead-in dwell carries its
> `dwell_start` timestamp, the frontend's slider extent can run earlier than the
> requested window; candidate Phase-5 refinement (clamp the stop's returned
> timestamp or the slider range). Next is Phase 5 (tune the knobs).

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
| C17 | Large-range decimation | **Size-aware, decimated per-kind** — always keep every `kind='stop'`; fill the remaining point budget with the highest-`importance` moving vertices | A year-scale view must still thin, but a huge dwell or a sharp turn must survive while a straightaway collapses. `importance` is *not* one scale across kinds (stop = dwell weight, moving = deviation-m), so rank *within* the moving kind: two-stage `ORDER BY importance DESC LIMIT` then re-sort by time. Stops are sparse and always survive; `truncated` then means "moving vertices were dropped." Replaces the dimensionally-broken `floor(span)` threshold. |
| C18 | Raw nav rate | **5 Hz via UBX-NAV-PVT, baud 38400** | Lane-level moving fidelity (~2.7 m/vertex at 30 mph). UBX's compactness keeps it within the baud budget so the baud doctrine is untouched; a rate-revert is graceful. See *Raw spatial resolution*. |
| C19 | Moving-regime thinning | **Online line simplification (Reumann–Witkam / open-window)** | Emit a vertex on perpendicular deviation > `simplify_epsilon`, not on raw distance — so straightaways collapse to endpoints and vertices land at genuine bends. The deviation magnitude is the per-vertex `importance` (C14). |
| C20 | Raw write cadence | **Tiered motion-gated writes** — 5 Hz moving, throttle to ~1 Hz (or 0.2 Hz) parked; room for more tiers keyed on speed/dynamics | 5 Hz parked is pure correlated bloat; the 5 Hz goal is moving sharpness. Cuts raw ~4–8× (~19→~5 GB/yr). One speed conditional on the existing write throttle; the freeze watchdog (tracks the fix *stream*, not writes) is unaffected. Cost: raw is no longer "every native fix" — a small, accepted purity hit. |
| C21 | Raw timestamp index | **Keep `idx_gps_points_timestamp`** | "No index needed" held only for the processor's rowid cursor. `/api/points/latest`, `/gpsd` (latest fix + frozen-window), `precache`, and `annotations.point_count` still range/order raw *by timestamp* (`api/db.py:57`); dropping the index full-scans a 38 M-row table. Cost is sub-GB/yr and bounded. Latest-fix readers *may* move to `ORDER BY id DESC` opportunistically (faster, index-free), but it's purely optional — the `now()`-sourced `timestamp` stays monotonic with `id` (C23), so `ORDER BY timestamp DESC` remains correct — and the range readers keep the index regardless. |
| C22 | UBX message set | **NAV-PVT at the nav rate + NAV-DOP & NAV-SAT throttled to ~5 s; NMEA disabled on UART1** | NAV-PVT alone fills `pdop`/`nsat_used`; `hdop`/`vdop`/`nsat_seen` need NAV-DOP + NAV-SAT. Throttled to ~5 s (the C9 cadence) they're negligible against the 38400 budget, so `receiver_metadata` is fully populated rather than half-NULL. NMEA off UART1 so 4-constellation GSV at 5 Hz can't blow the budget. **Conditional on Phase 0** verifying gpsd emits a populated `SKY` under UBX-only; if not, accept partial telemetry. |
| C23 | Timestamp source & order | **Keep wall-clock `now()` as the source (already PPS-disciplined stratum-1), extended to ms; `id` stays the determinism anchor; defensive negative-clamp in the processor** | The Pi's clock is chrony-disciplined to GPS+PPS (stratum 1, sub-µs), so `now()` *is* GPS-quality time — sourcing TPV `time` instead buys no accuracy and reintroduces backward jitter + an absent-value case (the logger reads TPV `time` only for the >10 s staleness guard, `logger/gps_logger.py:216`). `now()` is monotonic with `id` (chrony slews, doesn't step back in steady state), so `timestamp` stays monotonic with `id` and the "id == time order" assumption (C7) holds. Logger change is just the ms-precision extension — it already stamps `now()` (`logger/gps_logger.py:234`). The ~tens-of-ms receiver→logger latency is a near-constant offset (jitter sub-meter, below the ~1–2 m accuracy floor). Processor still clamps negative dwell/`dt` to 0 as cheap insurance against a rare chrony step / pre-PPS-lock boot. |
| C24 | Processor deploy | **`gps-processor.service`: enabled-gated restart (mirrors `mqtt-ingest`); manual Pi-side post-receive hook edit for the 5th unit; `PYTHONPATH` + `GPS_DB_PATH` env** | The hook lives in the Pi bare repo and hardcodes "four unit files," so committing the unit here doesn't teach it the fifth — an explicit Pi-side hook edit (install + enabled-gated restart) is a deploy step. Enabled-gating avoids crash-loops on hosts without the processor. The unit imports `api.db`, so it needs the same env as the others. |

---

## Open decisions

All resolved. *(Iteration history: P1→C11, P5→C9, P6→C8; P2→C12, P3→C13, P4→C14,
P7→C16, P8→C17, P9→C18, P10→C20. Iteration 5 added C21–C24 from the PR #1 codebase
review.)*

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
  hook (enabled-gated restart, like `mqtt-ingest` — not `gps-dashboard`'s always-on;
  it resumes from its cursor, so a restart is safe; C24). The hook is Pi-side and
  hardcodes four units, so teaching it the fifth is a manual edit (C24). CLAUDE.md
  deploy section updated when it lands.
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

- **Deterministic algorithm.** State transitions depend only on raw rows ordered
  by `id` (insertion order — the determinism anchor, per C23; the `now()`-sourced
  `timestamp` tracks `id` but `id` stays the canonical ordering key) and the fixed
  threshold config. No wall-clock in the *algorithm*, no RNG. The robust stop
  estimate and the line-simplification
  significance are *recompute-from-fixes* (same fixes → same result), never an
  order-dependent incremental accumulator.
- **Commit at safe boundaries.** The persisted cursor `last_committed_raw_id`
  advances only to the last *finalized* emit. An open stop, an open moving
  segment, and any un-emitted tail are **provisional**.
- **Restart.** Load the cursor, delete provisional `track_points`/`track_events`
  (`src_raw_id` beyond the cursor), reprocess forward → identical output. Replay
  cost is bounded by the longest open stop (below), not the tail.
- **Full rebuild.** Truncate `track_points` + `track_events`, set cursor → 0, run.
- **Caveat:** idempotency holds *per threshold set*. Changing a threshold is the
  intended trigger to rebuild — a feature, not a violation.
- **Timestamp source & order (C23).** The stored raw `timestamp` stays sourced from
  wall-clock `now()`, which on this Pi is chrony-disciplined to GPS+PPS (stratum 1,
  sub-µs) — already GPS-quality, so there's no reason to switch to the TPV `time`
  field (which can jitter backward and be absent; the logger reads it only for the
  >10 s staleness guard, `GPS_TIME_MAX_AGE_SECONDS`, `logger/gps_logger.py:15,216`).
  `now()` is monotonic with `id` in steady state (chrony slews, doesn't step back),
  so `timestamp` tracks `id` and determinism holds. The only logger change is
  extending the stamp to ms (`logger/gps_logger.py:234`). The processor still orders
  by `id` and clamps negative dwell/`dt` to 0 as insurance against a rare chrony step
  or a pre-PPS-lock boot.
- **Open-stop cost.** While the van is parked for days, no boundary finalizes, so
  the cursor never advances and a restart reprocesses the whole open dwell. At 1 Hz
  parked that is ~86 k rows/day — still fast, but replay cost is *bounded by the
  longest open stop*, not the loaded tail.

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

> **Message set — resolved (C22).** The baud budget above assumes UBX-NAV-PVT at the
> nav rate, so the default NMEA sentences are **disabled on UART1**
> (`CFG-MSGOUT-NMEA_*_UART1 = 0`) — otherwise NMEA (esp. GSV across 4 constellations
> at 5 Hz) competes for the link and blows the budget. The SKY-sourced
> `receiver_metadata` (C9) needs more than NAV-PVT (which carries only `pDOP`/`numSV`
> → `pdop`/`nsat_used`): **NAV-DOP** and **NAV-SAT** are also enabled, but throttled
> to ~5 s (the C9 cadence, not the nav rate), so the per-satellite NAV-SAT cost stays
> negligible against the budget and `hdop`/`vdop`/`nsat_seen` are fully populated.
> **Phase 0 must verify gpsd actually emits a populated `SKY` class under this
> UBX-only config** — the logger's SKY branch is a no-op otherwise; if it doesn't,
> fall back to partial `receiver_metadata`.

---

## Storage footprint (5 Hz)

Raw row ≈ ~135 B in-table (10 REAL accuracy/position fields + int mode + a
fixed-width ms timestamp string). The processor tails raw by its rowid cursor, so
*it* needs no timestamp index. (ms-text costs ~15 B/row over an integer epoch; with
motion-gating that delta is sub-GB/yr — consistency wins.)

> **Index — resolved (C21): keep `idx_gps_points_timestamp`.** "No timestamp index
> needed" holds only for the processor's rowid cursor. Other readers still
> range/order raw `gps_points` *by timestamp* and would full-scan a 38 M+-row table
> without the index (`api/db.py:57`): `/api/points/latest` orders by `timestamp DESC`
> (`api/routes/points.py:16`), the `/gpsd` page reads the latest fix + frozen-window
> (`api/routes/status_gpsd.py:79,106`), `precache.py:131` reads the latest fix, and
> the annotations list counts raw points inside each range
> (`api/routes/annotations.py:43`). The latest-fix readers *may* later move to
> `ORDER BY id DESC` (faster, index-free), but it's purely optional — `timestamp`
> stays `now()`-sourced and monotonic with `id` (C23) — and the range readers keep
> the index regardless.

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
  > **Resolved (Phase 4): stops match by dwell-interval overlap.** Moving vertices
  > are matched on `timestamp BETWEEN start AND end`, but a `kind='stop'` row is
  > matched on `dwell_end >= start AND dwell_start <= end` — its temporal footprint
  > is the whole dwell, not the single representative `timestamp` (which equals
  > `dwell_start`). Without this, a long open dwell falls out of any window that
  > begins after the dwell started, so "last 1h" while parked overnight renders
  > empty even though the van is parked there *now*.
- `/api/points/latest` keeps reading raw `gps_points` (live dot = true current fix).
- `?bucket=` reworked to **size-aware** decimation (C17): for huge spans, keep every
  `kind='stop'` and fill the remaining point budget with the highest-`importance`
  moving vertices, rather than grouping blindly by time.
  > **Resolved (C17): per-kind, not one scale.** `importance` is *not* comparable
  > across kinds — a stop's is a dwell weight (seconds/log-seconds), a moving vertex's
  > a perpendicular deviation (metres) — so the old `kind='stop' OR importance >=
  > floor(span)` was dimensionally broken (it mixed a time-span with deviation-metres).
  > Instead: always include stops (sparse, always wanted), then a two-stage query
  > `ORDER BY importance DESC LIMIT` over the moving vertices (deviation-metres *is*
  > comparable within that kind) and re-sort by time for rendering. The current handler
  > bounds output with `ORDER BY timestamp ASC LIMIT ?` and reports `truncated` when
  > `len == limit` (`api/routes/points.py:79,94`); under C17 `truncated` means *moving
  > vertices were dropped* — stops always survive.
- **The annotations list still range-queries raw** (`api/routes/annotations.py:43`):
  `point_count` is `COUNT(*)` over `gps_points WHERE timestamp BETWEEN start AND end`.
  Moving the trail to `track_points` does *not* move this. **Resolved: keep it on
  raw** — the count stays an honest "how many underlying fixes," and C21 keeps
  `idx_gps_points_timestamp` so the O(range)-per-annotation query (on a page that
  lists *every* annotation) stays fast. Not re-pointed at `track_points`/`n_raw`:
  that would silently change the count's meaning to "processed points."
- A future `/api/events` (or inclusion in the points payload) surfaces `track_events`.
- Frontend trail rendering still consumes a point list; stop rows / high-`n_raw`
  points can get a distinct marker (dwell pin) sized by `n_raw`.

---

## Deploy & ops impact

- New `deploy/gps-processor.service` (enabled-gated restart in the post-receive
  hook, matching `mqtt-ingest` — CLAUDE.md: the hook always restarts
  `gps-dashboard` and restarts `mqtt-ingest` *if enabled*; mirror that so a host
  without the processor enabled never crash-loops).
  > **Resolved (C24).** The post-receive hook lives on the **Pi bare repo**
  > (`/mnt/nvme/gps-dashboard.git`), **not in this repo**, and per CLAUDE.md it
  > hardcodes reinstalling *"all four unit files"*. Committing
  > `deploy/gps-processor.service` here does **not** teach the hook about a fifth
  > unit — install + enabled-gated restart for it is a manual Pi-side hook edit,
  > called out as an explicit Phase-2 deploy step below. The unit also needs
  > `PYTHONPATH` + `GPS_DB_PATH` env like the other units (it imports `api.db`).
- Restart is safe at any time — the processor resumes from `processing_state` and
  recomputes the provisional tail (C7).
- Heartbeat to the journal: emitted track points, emitted/updated stops, events
  emitted, raw rows consumed, dropped-by-accuracy count.
- CLAUDE.md (Processes, Data Model, Project Structure, Deploy) updated when it lands.

---

## Phasing / action items

- [x] **Phase 0 — Receiver config.** Set 5 Hz + UBX-NAV-PVT on the M9N (ubxtool),
      disable NMEA on UART1, enable NAV-DOP + NAV-SAT throttled to ~5 s (C22), persist
      to flash. Confirm gpsd reports 5 Hz TPV with `epx`/`epy` **and a populated `SKY`
      class** (hdop/vdop/nsat) under the UBX-only config; if `SKY` is absent, fall back
      to partial `receiver_metadata` (C22). Decouple-able from the rest (graceful
      fallback to 1 Hz NMEA).
- [x] **Phase 1 — Schema + logger.** Add `gps_points` TPV columns, `track_points`,
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
- [x] **Phase 2 — Processor skeleton.** `gps-processor` service: tail raw by the
      committed cursor, **copy-through** to `track_points` (no denoise yet) to
      validate plumbing, cursor persistence, WAL concurrency, deploy — and prove
      idempotency (run twice, diff output). **Deploy step (C24):** commit
      `deploy/gps-processor.service` (with `PYTHONPATH` + `GPS_DB_PATH` env), then
      manually edit the Pi-side post-receive hook to install the 5th unit and
      enabled-gate-restart it; update CLAUDE.md's deploy section (four→five units).
- [x] **Phase 3 — Online filter.** Implement the parked/moving state machine
      (static hold with continuous refinement + online line simplification +
      accuracy gating), `n_raw`/`importance` tagging, `track_events` emission,
      safe-boundary commits.
      **Deploy step:** after pushing, run one `uv run processor/gps_processor.py
      --rebuild` on the Pi (stop the service first) — the Phase-2 skeleton left
      ~269 k copy-through `track_points` and advanced the cursor past them, so a
      plain resume would leave copy-through history below the cursor and denoised
      history above it. One rebuild reprocesses all raw through the filter (C7).
- [x] **Phase 4 — Wire the frontend.** Point `/api/points` at `track_points`;
      keep `/api/points/latest` on raw; rework `?bucket=` to size-aware; confirm
      live + history both behave. **Stops are matched by dwell-interval overlap,
      not representative timestamp** — surfaced in verification: a long open dwell
      timestamps its row at `dwell_start`, so a recent window (e.g. "last 1h" while
      parked overnight) rendered empty until the query switched to
      `dwell_end >= start AND dwell_start <= end`. Verified end-to-end against a Pi
      DB snapshot (273 k raw / 1015 track_points) via headless Chrome: history
      renders the denoised trail, live renders the open dwell, no JS errors.
- [ ] **Phase 5 — Tune.** Calibrate the knobs against the marked Chick-fil-A trip
      and a parked-overnight window; eyeball before/after.
- [ ] **Phase 6 (future) — Events in the UI.** Surface `track_events` as suggestions;
      optionally promote to `annotations`.

### Phase 1 note — timestamps & dedup at 5 Hz

`canonical_timestamp` currently emits whole-second UTC; at 5 Hz that collides and
gives no sub-second dedup key. Extend it to **fixed-width ms**
(`%Y-%m-%dT%H:%M:%S.%fZ`, 3-digit fraction) while **keeping the source as wall-clock
`now()`** (PPS-disciplined stratum-1, C23 — not TPV `time`); at ms precision `now()`
is distinct per write at 5 Hz, so it doubles as the dedup key. Keep the format
**uniform across tiers** so lexical range comparisons stay correct — mixing widths
breaks ordering (`'.'` sorts before `'Z'`), so a one-time migration rewrites existing
whole-second rows to `.000Z`. Cheap in a prototype; flag for review before implementing.

> **Phase-1 impl — writer-site audit.** Extending `canonical_timestamp` alone is *not*
> enough: several writers build the whole-second string directly with
> `strftime('%Y-%m-%dT%H:%M:%SZ')` and bypass the function. Each must move to ms in
> the same change, or it re-introduces the width mismatch and the `'.'`<`'Z'` hazard:
> the logger INSERT (`logger/gps_logger.py:234`), the `marks` upsert
> (`api/routes/annotations.py:162`), and the frozen-window cutoff the `/gpsd` page
> compares against raw (`api/routes/status_gpsd.py:103` — a whole-second cutoff vs.
> ms-stored rows drops the cutoff-second's points, since `.000Z` < `Z`).
>
> **Phase-1 impl — migration cost.** The one-time rewrite of existing rows is a
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
