# DRY / simplification pass

Status: **PAUSED — resume at Phase 3.** Recon complete (6-agent read-only sweep, 2026-07-19).
Backend (Phases 1–2) landed across 11 commits in session 1 (2026-07-19); frontend + hot-path +
structural work (Phases 3–5) deferred to a fresh session.

- ✅ **Phase 1 DONE** (A1, B1–B4 committed; ruff+mypy clean).
- ✅ **Phase 2 DONE** (C2, C3, C4+C5, C1, C6 + gpsd_validate minor committed; 812 tests green).
  **C7 — SKIPPED (confirmed 2026-07-19).** See "Deliberately NOT doing".
- ⏭️ **RESUME HERE → Phase 3** (frontend mechanical: helpers + CSS consolidation), then Phase 4
  (sensors hot paths, test-first, all 🔥), then Phase 5 (structural — each needs a 👍 first).

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

## Phase 3 — Frontend mechanical (helpers + CSS) — ⏭️ RESUME HERE

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

## Phase 4 — sensors hot paths  (test-first; all 🔥)

### D1 — `publish_reading` / `publish_status` helpers  [M, L, 🔥] · ~13 LOC + deletes ~8 `# type: ignore`
Centralize `qos=1, retain=True` + ts-stamping; typed → removes the `# type: ignore` noise. De-risks D2.

### D4 — `used_percent(total, free)` primitive  [M, L, 🔥] · ~6 LOC
Dedup `system_reader.py:77` core ≡ `dahua_reader.py:166`.

### D3 — Dahua RPC scaffolding helper  [M, L→M, 🔥] · ~11 LOC
`_rpc(device, columns, fn)` + `_clock_offset(device)` for the repeated session/except/clock blocks.

**core minor (🔥):** import `GPSD_HOST/PORT/WATCH` from `common.gpsd` in `logger:217` (constants only);
optionally move `processor._distance_m` → `simplify.distance_m`.

### D2 — Unify Victron/Fridge status-owning loop  [S, M, 🔥] · ~40 LOC  *(propose first)*
`run_gated_publisher(source, ...)` in `runner.py` where `source.read()->Mapping|None`. Covered by
`tests/test_{victron,fridge}_reader.py::test_publish_loop_*`.

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

### F11 — Backend decode/threshold drift  [S, M→H] · ~25 LOC  *(correctness, not cosmetics)*
Home/Drive re-encode `METRIC_META` enum labels, the throttle bitmask, and the OBD engine-on gate.
Prefer serving labels from `METRIC_META`; hoist the engine-on gate into one helper. Do incrementally.

### D5 — Victron columns from schema  [S, M] · ~20 LOC  *(layering decision)*
`VICTRON_COLUMNS` = `READING_TABLES['victron']['metrics']` instead of a copy + drift-guard test.
Introduces a `sensors → api` import edge that doesn't exist today — **needs a call on layering.**

---

## Deliberately NOT doing  (flagged by recon so we don't relitigate)

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

### Remaining (next session): Phases 3–5

Phase 3 (frontend mechanical), Phase 4 (sensors hot paths — test-first), Phase 5 (structural, each
approved first). Est. remaining reduction is the larger share (~500–600 LOC), concentrated in the
Phase 3 CSS consolidation and the Phase 5 layout components (F6 `StatusCheckPage`, F5 `<Toast>`,
F7 `poll()`). Open user decisions before their items: **D5** (sensors→api import edge) and **F11**
(serve `METRIC_META` decode labels from the server).
