# DRY / simplification pass

Status: **PAUSED — resume at Phase 5.** Recon complete (6-agent read-only sweep, 2026-07-19).
Backend (Phases 1–2) landed in session 1; frontend (Phase 3) in session 2; sensors hot paths
(Phase 4) in session 3 (all 2026-07-19); structural work (Phase 5) is what remains.

- ✅ **Phase 1 DONE** (A1, B1–B4 committed; ruff+mypy clean).
- ✅ **Phase 2 DONE** (C2, C3, C4+C5, C1, C6 + gpsd_validate minor committed; 812 tests green).
  **C7 — SKIPPED (confirmed 2026-07-19).** See "Deliberately NOT doing".
- ✅ **Phase 3 DONE** (session 2: F1, E2, E3, E1, F8, E4, F2, F3 — 8 commits; Vitest 134→138, check
  clean). **F4 — SKIPPED (user call 2026-07-19):** Fridge/Radio's `.card` collides with the existing
  global `.card` and drifts from it, so sharing needs a rename + markup churn across both views for
  modest LOC. See "Deliberately NOT doing". F3 was trimmed to the **7 byte-identical** label sites;
  the ~10 size/color variants recon lumped in are genuinely distinct and left alone.
- ✅ **Phase 4 DONE** (session 3: D1, D4, core-minor logger constants, D2 — 4 commits; 811 tests green,
  ruff+mypy clean tree-wide). **D3 — SKIPPED (user call 2026-07-19):** the Dahua RPC scaffolding is
  untested (tests stub `_rpc_*` at the method boundary), network-touching, deployed, and the
  `_clock_offset` half is behavior-risky on the per-device early-out — ~10 LOC for real risk. The
  optional `processor._distance_m → simplify.distance_m` move was also skipped (single-use relocation,
  not a dedup). See "Deliberately NOT doing".
- ⏭️ **RESUME HERE → Phase 5** (structural — each needs a 👍 first). **D5 + F11 already approved**
  (user, 2026-07-19); the rest await per-item approval.

**Fresh-session pickup notes:**
- Phase 3+ is frontend: changes land in `web/src/`; run `npm run build` + Vitest and **commit the
  regenerated `static/dist/`** (the Pi never builds). Each frontend commit carries a rebuilt bundle.
- Nothing has been pushed — session-1 commits sit on local `main`; deploy (`git push all main`) is the
  user's to trigger.
- Working style: mechanical wins committed directly (small focused commits, tests first); every Phase 5
  structural item is proposed for approval before touching. Two Phase 5 design calls still open for the
  user: **D5** (sensors→api import edge) and **F11** (serve decode labels from `METRIC_META`).

Goal: reduce LOC and complexity, and kill cross-module drift surfaces, without over-abstracting.
Appetite (agreed): execute **mechanical** clear wins directly; **structural** items are proposed and
approved before touching. Deployed hot paths (logger/processor/mqtt readers) are fair game but
**test-first** — verify behavior unchanged before deploy.

Legend: **[M]** mechanical (do directly) · **[S]** structural (propose first) · risk L/M/H · 🔥 hot path.
LOC figures are rough per-finding estimates from recon.

---

## Cross-cutting themes (the high-leverage stuff)

Two findings surfaced from *multiple independent* agents and touch the most sites:

- **Canonical/ISO timestamp parsing** — the inverse of `common/timefmt.canonical_timestamp` is
  reimplemented ~13× as `datetime.fromisoformat(x.replace('Z','+00:00'))` (+`.timestamp()` or age
  subtraction), including 4 named private helpers that are byte-identical. Flagged by the api, core,
  and (implicitly) sensors sweeps. **This is item A1 — do it first**, it unblocks many call-site swaps.
- **Unit-conversion constants** (mps→mph, °C→°F, m→mile) live in parallel copies frontend *and*
  backend, and 2–3× within the frontend. Items E3 + A-note.

---

## Phase 1 — Backend shared primitives  (mechanical foundation) — ✅ DONE

### A1 — Canonical-timestamp parse family in `common/timefmt.py`  [M, L→M, 🔥] · ~40 LOC
Add `parse_iso(v)->datetime`, `epoch_seconds(v)->float`, `age_seconds(v, now=None)->float`, and a
public `format_canonical(dt)` (promote the private `_canonical`). Replace the duplicated private
helpers and inline `.replace('Z','+00:00')` idiom at:
- 🔥 `processor/gps_processor.py:203` `_ts_seconds`, `logger/gps_logger.py:321`
- `api/observatory.py:47` `unix_seconds`, `api/routes/obd.py:24` `_epoch_seconds`,
  `api/routes/sensors.py:129` `_canonical_to_epoch_s`, `api/routes/status_gpsd.py:62,63,98,164`,
  `api/routes/fridge.py:267`, `mqttbus/ingest.py:135`, `updater/chunks.py:193` `_age_days`
- `radio/recorder.py:51,292` (also stop importing `_canonical` via `api.db`; use `common.timefmt`),
  `updater/probes.py:27`
Additive to the writers → no hot-path behavior change; verify vs `tests/test_{logger,timestamps}.py`.

### B1 — Finish `api/params.py`: time-window parsers  [S small, L] · ~35 LOC
`parse_required_window(args)` (require both start+end) and `parse_time_window(args, default_hours)`
(default-window idiom). Rewire `points.py:82`, `obd.py:39` (required); `sensors.py:87,208`,
`globe.py:48` (default-window). Keep the `start>=end` guard inline where only one site has it.

### B2 — `api/params.py`: bbox + interval-overlap WHERE builders  [M, L] · ~22 LOC
`bbox_point_where(bbox, prefix='')` (`points.py:104`, `phone.py:161`, `places.py:290`),
`bbox_overlap_where(bbox)` (`drone.py:150`, `phone.py:89`), and generalize phone's
`_time_overlap_where` → `time_overlap_where(args, start_col, end_col)` (also `drone.py:133`).
Kills the `[s,n,w,e]` vs `[w,e,s,n]` transposition footgun.

### B3 — Public `params.error(msg, status=400)`  [M, L] · ~6 LOC
Promote the private `params._error`; delete the identical `_err` copies in `fridge.py:50`,
`radio.py:68`; import in `places.py`. (Optionally sweep ~15 inline `jsonify({'error':...})` sites.)

### B4 — Shared sensor-read helpers  [M, L] · ~13 LOC
Promote `sensors.py:29` `_latest_reading` to public (beside `READING_TABLES` in `api/sensor_schema.py`
or a small `api/sensors_read.py`); `fridge.py:170` reuses it. Optionally reuse `status.py:45` `_latest`
in `points.py:26` / `status_gpsd.py:26` (preserve points' 404 + `id`, status_gpsd's try/except).

---

## Phase 2 — tools/ dedup — ✅ DONE (C7 skipped)

### C2 — `--db`/`--places-db` override helper  [M, L] · ~18 LOC
`api.db.apply_path_overrides(db=None, places_db=None)` (+ maybe `add_db_argument(parser)`) replacing
the global-mutation idiom in 8 tools (fetch_wikipedia, import_drone, passes_validate, tle_validate,
import_places, import_phone_timeline, radio_vox_replay).

### C3 — `ssh_reachable(host, connect_timeout=8)` in `common/proc.py`  [M, L] · ~12 LOC
Dedup `backup_db.py:146` `preflight` ≡ `import_drone.py:361` `SshDockerExtractor.preflight`.

### C4 — Import `parse_zoom` instead of copying  [M, L] · ~6 LOC
`fetch_terrain_tiles.py:53` copy → import from `tools.precache` (already imports its siblings).

### C5 — `parse_bbox(s)` in `tools/regions.py`  [M, L] · ~8 LOC
Dedup `precache.py:195` ≡ `fetch_terrain_tiles.py:334` `_resolve_bbox` (raise ValueError; click
callers wrap to BadParameter).

### C1 — Backtest validators shared helpers  [S mod, L] · ~45 LOC  *(biggest tools win)*
Move `_percentile`, `_split`, `print_error_table(...)`, `load_observation_tracks(conn, hours)` into
`api/observatory.py` (both files already import from it). Dedup `passes_validate.py` ≡ `tle_validate.py`.
Covered by existing `tests/` for passes_validate.

### C6 — Share the rate-limiter  [S small, L→M] · ~15 LOC
Promote `precache.py:26` `RateLimiter` to a shared module; give `fetch_wikipedia.py:251` an instance
instead of `global _next_slot` module state (thread it onto the session).

### C7 — `as_completed_or_cancel(futures)` in `common/cli.py`  [S, M] · ~12 LOC
Absorb the `try/except KeyboardInterrupt: cancel pending; exit 130` loop (fetch_wikipedia, import_drone
— drop its `_as_completed` shim — precache). Keep each caller's partial-stats print and
`fetch_wikipedia`'s interrupt-time `_flush` caller-side.

**tools/ minor:** `gpsd_validate.py:12` `check_service` → `common.proc.service_state` (1-line).

---

## Phase 3 — Frontend mechanical (helpers + CSS) — ✅ DONE (session 2; F4 skipped)

### F1 — `errMsg(e: unknown): string` helper  [M, L] · ~15 LOC
`e instanceof Error ? e.message : String(e)` repeated ~19× across views → one helper (`lib/errors.ts`
or `geo.ts`).

### E2 — Share `emptyFC()` (×3) and `escapeHtml()` (×2)  [M, L] · ~12 LOC
Byte-identical copies in `map.ts`/`phone.ts`/`places.ts` → `lib/geojson.ts` (or geo.ts).

### E3 — Consolidate unit conversions into `geo.ts`/`sensors.ts`  [M, L] · ~13 LOC
skyplot `mps→mph` → `fmtSpeed`; fridge/Home/Drive `°C→°F` → shared `celsiusToF`. (lib#5 + views#11.)

### E1 — `setGeoJSON(name, data)` closure in `map.ts`  [M, L] · ~15 LOC
Collapse 19× `(map.getSource(x) as GeoJSONSource|undefined)?.setData(y)`.

### F8 — Places helpers  [M, L] · ~15 LOC
`localDate(offsetDays)` (verbatim ×2) → `lib/geo.ts`; `eventDateLabel(ev, opts?)` → `lib/places.ts`
(parameterize separator/"+N more").

### E4 — `qs(obj)` query-string helper in api.ts  [M, L] · ~12 LOC
Skip-nullish + stringify for ~10 `new URLSearchParams` sites (callers keep array-join/key-rename).

### F2 — `.banner`/`.banner.ok`/`.banner.err` → `app.css`  [M, L] · ~60 LOC
Byte-identical in Gpsd/Ntp/Fridge/Radio; keep Docs' neutral variant local.

### F3 — `.eyebrow` uppercase-label utility → `app.css`  [M, L] · ~40 LOC
~11 copies of the small-caps label recipe under a dozen class names.

### F4 — Shared control-plane CSS (`.card`/`.seg`/`button.primary`/…)  [M, L→M] · ~80 LOC
Fridge ≡ Radio control-panel visual language → `controls.css`/`app.css`.

---

## Phase 4 — sensors hot paths  (test-first; all 🔥) — ✅ DONE (D3 skipped)

### D1 — `publish_reading` / `publish_status` helpers — ✅ DONE
Typed `publish_status`/`publish_reading` in `runner.py` centralize `qos=1, retain=True`; removed all 8
`# type: ignore[attr-defined]` (victron/fridge/obd). ts-stamping left caller-side (victron/fridge fold
ts + history into `build_snapshot`). The client seam was later narrowed to a `Publisher` protocol
(commit with D2) so the stubs type-check without a suppression.

### D4 — `used_percent(total, free)` primitive — ✅ DONE
`used_percent(total, available)` in `runner.py` dedups `parse_mem_used_pct` ≡ `dahua.mem_used_pct_from`.
`disk_usage`'s block-based df formula left alone (different calc).

### D3 — Dahua RPC scaffolding helper — ⏭️ SKIPPED (see "Deliberately NOT doing")

**core minor (🔥):** — ✅ DONE (logger). Promoted `common.gpsd._WATCH → WATCH`; `logger` imports
`GPSD_HOST/PORT/WATCH` (constants-only, provably equivalent). The optional `processor._distance_m →
simplify.distance_m` move was skipped (single-use relocation, not a dedup).

### D2 — Unify Victron/Fridge status-owning loop — ✅ DONE
`run_gated_publisher(*, read: Callable[[], str|None], stale_label, heartbeat_context, ...)` in
`runner.py` (session-owning sibling of `run_simple_publisher`). Each reader keeps a small
`read_snapshot()` adapter; `main()` collapses to one call. Shared flip-on-transition path pinned once
in `test_runner`; per-reader `read_snapshot` tests replace the three `test_publish_loop_*`.

---

## Phase 5 — Structural proposals  (each needs a 👍 before I touch it)

### F6 — `StatusCheckPage` layout component  [S, M] · ~120–150 LOC  *(largest single reduction)*
Gpsd + Ntp are near-identical check-driven status shells (shared `{overall_ok, checks[]}` contract).
Extract banner + Checks list + `.kv`/`.panel` CSS; views become thin bodies.

### F7 — `poll(fetcher, intervalMs)` rune helper  [S, M] · ~60 LOC
Dedup the onMount/refresh/setInterval/onDestroy lifecycle in Home/Systems/Gpsd/Ntp/Data. Leave
Fridge/Radio (extra read-back timers).

### F5 — `<Toast>` component / `useToast()`  [S, L→M] · ~55 LOC
Fridge ≡ Radio toast state+fn+CSS (only the timeout differs).

### F9 — `<DataAgeBanner>` + `fetchRecord(idAccessor, fetcher)`  [S, L→M] · ~30 LOC
PlaceDetail ≡ EventDetail age-banner + id-guarded fetch; share `STALE_DAYS`.

### F10 — Consolidate 4 duration formatters  [S, M] · ~20 LOC
Home.dur/Sky.humanGap/InspectPanel.fmtSecs/geo.fmtDuration → `fmtDurationSecs(s, {days,padMin,showSecs})`.
Behavior-preserving merge — verify each rendered string.

### E5 — Fold `postRadio`/`postFridge` into `sendJSON`  [S small, L] · ~18 LOC
Standardize the error-fallback string (or add a fallback param).

### E6 — One GNSS name+color source  [S, L→M] · ~15 LOC + kills a 3-way sync surface
`lib/gnss.ts` used by globe/skyplot/Sky.svelte; prefer trusting server `system`/`name` fields where
present. Names also mirror `common/gpsd.py:30`.

### E7 — Typed api.ts wrappers for globe/skyplot  [S, M] · ~15 LOC + types 2 `any` payloads
`getConstellation(hours)`, `getGpsdSky()`; extend `getPasses` for the `track` flag.

### F11 — Backend decode/threshold drift  [S, M→H] · ~25 LOC  *(correctness, not cosmetics)* — 👍 APPROVED
Home/Drive re-encode `METRIC_META` enum labels, the throttle bitmask, and the OBD engine-on gate.
Prefer serving labels from `METRIC_META`; hoist the engine-on gate into one helper. Do incrementally.

### D5 — Victron columns from schema  [S, M] · ~20 LOC — 👍 APPROVED (sensors → api import OK, user 2026-07-19)
`VICTRON_COLUMNS` = `READING_TABLES['victron']['metrics']` instead of a copy + drift-guard test.
Introduces a `sensors → api` import edge; user confirmed the layering (a sensors→api dep is logical).

---

## Deliberately NOT doing  (flagged by recon so we don't relitigate)

- **D3 — Dahua RPC scaffolding** (`_rpc`/`_clock_offset` in `dahua_reader.py`): *skipped, user call
  2026-07-19.* Two reasons. (1) Zero test coverage of the code it'd touch — `test_dahua_reader.py`
  stubs `_rpc_nvr_metrics`/`_rpc_camera_metrics` at the method boundary, so the RPC session, the
  try/except, and the empty-fill path are never exercised; refactoring an untested, network-touching,
  deployed reader fails the test-first-on-hot-paths bar. (2) The `_clock_offset(device)` half is
  behavior-risky — the current-time fetch sits *inside* each `_read_*` try that drives the whole-device
  early-out, so extracting it changes the drop semantics. Net ~10 LOC for real risk on a reader that
  can't be driven from the dev laptop (fleet's on the van LAN). The one safe slice (a `_host_metrics`
  for the cpu/mem pair) nets ~-3 LOC for an added method — not worth it alone.
- **`processor._distance_m → simplify.distance_m`** (Phase 4 core-minor, optional): *skipped.* It's a
  single-use function (processor only), so moving it to `simplify` is a lateral relocation, not a
  dedup — it kills no drift surface. Left in place.
- **F4 — shared control-plane CSS** (Fridge/Radio `.card`/`.seg`/`button.primary`): *skipped, user call
  2026-07-19.* Fridge/Radio's local `.card` is a variant (14px pad + margin-bottom) that **collides
  with and locally overrides the existing global `.card`** (16px, no margin) — so sharing it means a new
  distinct name (`.ctl-card` etc.) + a rename across both views' markup, plus verifying `.seg`/`.primary`
  actually agree. Markup churn + collision risk for ~80 LOC across two views; not worth it. The clean
  slice of F-CSS (the banner F2 + the eyebrow F3) landed instead.
- **F3 variants** — recon's "~11 eyebrow copies" was really **7 byte-identical** dominant-recipe sites
  (done) plus ~10 genuine variants (10px `.tag`, 12px section heads, 13px `.panel-name`, skyplot's
  `--sp-muted`, places/timeline headers). The variants differ in size/color and are left alone.
- **C7 — ThreadPoolExecutor cancel helper** (precache / fetch_wikipedia / import_drone): *skipped,
  confirmed 2026-07-19.* The only shared code is a 2-line `for f in futures: f.cancel()` loop; each
  interrupt handler's real cleanup differs (precache: flag + post-pool stats print + `sys.exit(130)`;
  fetch_wikipedia: DB `_flush` + resume hint + `sys.exit(130)`, plus a *second* cancel on its
  network-down streak-abort; import_drone: `_print_summary` + `return 130`). A shared cancel-and-
  re-raise generator saves ~6 lines while adding control-flow indirection — trades duplication for
  complexity, and each caller keeps a bespoke `except` regardless.
- **`_apply` session-wrappers** (fridge vs radio): differ on retry/exception mapping — merging needs
  3+ params, trades dup for parameter complexity.
- **child-point-embedding / importance-decimation** (drone vs phone vs points): shared silhouette,
  but the per-domain matching logic is the substance.
- **Reconnect/backoff supervisor loop** (logger/processor/recorder): each threads bespoke restart
  state; shared part is ~6 lines, net ~0 after callback plumbing, 2 hot paths. Revisit only if a 4th
  daemon appears.
- **`EARTH_RADIUS_M` (×2)**: independent geometry modules; merging couples them for one constant.
- **Generic Svelte store factory / overlay base class**: only `annotations` fits; others are
  genuinely bespoke and would fight the `$state` idiom.
- **stdlib-only Pi probes** (`civ_probe`, `cfx3_probe`) hand-rolling KeyboardInterrupt: by design —
  they can't import `common` (no venv on the Pi). Leave.
- **Per-env-var accessors → `env_int/env_str`**: loses per-var docstrings for ~1 line each.
- **ingest per-type `with_derived_humidity` branch / two heartbeat counters**: a registry for one
  type today is over-abstraction; sharing the heartbeat would reverse the `mqttbus`→`sensors` dep.

---

## Rollup

~30 actionable findings; rough ceiling **~800–900 LOC** removed plus ~6 drift surfaces eliminated.
Biggest single reductions: F6 (~130), F4 (~80), F2 (~60), F7 (~60), F5 (~55), C1 (~45), D2/A1 (~40).
Verification per phase: `uv run pytest` + `ruff` + `mypy` (backend); `npm run build` + Vitest +
rebuild/commit `static/dist/` (frontend). Hot-path items (A1, D*, core) gated on their existing tests.

### Session 1 landed (2026-07-19) — backend only

11 commits on local `main` (unpushed). Suite grew 790 → 812 (added parse-family, params-helper,
ssh_reachable, parse_bbox, and backtest-helper tests); ruff + mypy clean throughout. New shared homes:
`common/timefmt` parse family (`parse_iso`/`epoch_seconds`/`age_seconds`/`format_canonical`),
`api/params` (`parse_required_window`/`parse_time_window`/`bbox_point_where`/`bbox_overlap_where`/
`time_overlap_where`/public `error`), `api/sensors_read.latest_reading`,
`api/db.apply_path_overrides`, `common/proc.ssh_reachable`, `tools/regions.parse_bbox`,
`tools/backtest_common`, `tools/ratelimit.RateLimiter`. B3 also fixed a latent
`UnboundLocalError` in `fridge.history` (an `error` local shadowed the promoted helper).

### Session 2 landed (2026-07-19) — frontend (Phase 3)

8 commits on local `main` (unpushed, stacked on session 1). New shared homes: `lib/errors.errMsg`;
`lib/geo` grew `emptyFC`/`escapeHtml`/`mpsToMph`/`metersToFeet`/`celsiusToF`/`localDate`; `lib/places`
grew `eventDateLabel` (parameterized, tested); `lib/api` grew `qs()`; `lib/map` grew a `setGeoJSON`
closure (19 call sites); `app.css` grew `.status-banner` (F2) + `.eyebrow` (F3). Vitest 134→138 (added
`eventDateLabel` cases); `svelte-check`+`tsc` clean; each commit carries a rebuilt `static/dist/`. **F4
skipped** (see "Deliberately NOT doing"). Two on-device visual glances worth doing post-deploy: the
status banner (Fridge/Radio +2px pad normalization) and the eyebrow labels render unchanged.

### Session 3 landed (2026-07-19) — sensors hot paths (Phase 4)

4 commits on local `main` (unpushed, stacked on sessions 1–2). Suite 812→811 (net one fewer after
consolidating three per-reader loop tests into one shared `test_runner` flip test + per-reader
`read_snapshot` tests); ruff + mypy clean tree-wide. New shared homes in `sensors/runner.py`:
`publish_status`/`publish_reading` (typed, kill 8 `# type: ignore`), `used_percent`, a `Publisher`
protocol (the narrowed publish seam), and `run_gated_publisher` (the unified Victron+fridge status
loop). `common.gpsd._WATCH → WATCH` (logger sources the gpsd socket constants). **D3 + the optional
`_distance_m` move skipped** (see "Deliberately NOT doing"). Trap avoided: run full-tree `mypy .`
(not just `mypy sensors/`) after typing a hot-path signature — `tests/` type-checks against it, and
`mypy sensors/` alone missed the obd test's stale stub-arg type (the `Publisher` seam fixed it).

### Remaining (next session): Phase 5 (structural)

Each item needs a 👍 first; **D5 + F11 already approved** (sensors→api import edge OK; serve
`METRIC_META` decode labels + hoist the OBD engine-on gate from the server). Biggest remaining
reductions are the layout components: F6 `StatusCheckPage` (~130), F5 `<Toast>`, F7 `poll()`. Also
F9, F10, E5, E6, E7 open. All frontend except D5/F11 (backend) — frontend commits must rebuild +
carry `static/dist/`.
