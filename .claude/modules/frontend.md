# Frontend

**Van OS** — a client-side SPA (Svelte 5 + Vite + TypeScript) in `web/`, built to
`static/dist/` (committed) and served by Flask. A persistent nav shell with eight
top-level destinations (**Home · Map · Drive · Places · Systems · Docs · Sky ·
Radio**); the map is one tab among several, not the privileged single view it once was.
Mobile-first (the primary client is a phone over the van's WiFi): a bottom tab bar on
phones, a left sidebar on desktop. (Tab-bar trap: flex items default to
`min-width:auto`, so a wide label would push the last tab off-screen — `.nav li` sets
`min-width:0` + label ellipsis. And headless Chrome on macOS floors the window at
**500 px wide**, cropping `--screenshot` below that — measure via CDP, don't trust
narrow screenshots.)

## Build & serve

- **Source:** `web/` (`web/src`, `web/vite.config.ts`, `web/package.json`).
  `node_modules` gitignored; **build output `static/dist/` is committed**.
- **Dev:** `cd web && npm run dev` (Vite + HMR) with `/api`+`/tiles` proxied to a local
  Flask (`uv run python -m api.app`, on a non-5000 port — `VITE_API_TARGET`). `npm run
  check` runs svelte-check + tsc.
- **Prod:** `npm run build` → `static/dist/` → Flask `send_file`s `dist/index.html` for
  `/` and every non-`api`/`tiles`/`static` path (`api/app.py` catch-all), so client-side
  deep links resolve. **New discipline: rebuild + commit `static/dist/` before `git push
  all`** — the Pi never builds (offline constraint is runtime-only; see
  [[offline-constraint-interpretation]] in memory).
- **Vendoring → npm.** MapLibre, three, pmtiles are npm deps Vite bundles, as are the
  chart deps (LayerCake + d3-scale/d3-shape; the committed bundle is still fully offline).
  Heavy deps are **dynamic-imported** so each heavy view is its own lazy chunk and the
  main bundle stays small: `map` (MapLibre), a shared `three` chunk (`globe` +
  `overlay3d`), and the LayerCake Trends chart. The basemap **data** assets
  (`static/vendor/basemap/` style/glyphs/sprite) stay served as static files.

## Shell, routing, state

- **Shell** (`web/src/lib/Shell.svelte`) — persistent nav chrome + active-tab highlight.
  A `RouteDef.tab` maps a sub-route (`/ntp`, `/gpsd`, `/globe`, …) to its parent tab.
- **Router** (`web/src/lib/router.svelte.ts`, `routes.ts`) — a tiny History-API router
  (no dep); pushState + popstate, so canvas/WebGL state survives tab switches.
- **Home** (`web/src/views/Home.svelte`) — the status glance, from `GET /api/status` (one
  aggregate read: latest fix + mode, Victron SOC/solar/load, OBD if engine recently on,
  cabin IAQ/temp, GNSS sat count/fix health, systemd service states); 5 s poll, per-domain
  staleness.
- **Stores** (`web/src/lib/stores/`) — `selection.svelte.ts` is the **global time axis**:
  the window (`mode: last|range`, anchor/from/to, live + a 30 s live tick) **is the only
  time object** (the old sub-window brush and `around` mode are gone — time-dock rework,
  2026-07). It also owns the **zoom history** (`zoomTo` pushes / `back` pops / `resetZoom`;
  backing out of a zoom taken from Live *resumes* Live), nav helpers (`shift(±1)`,
  `widen()` — 2× around center, capped `MAX_WINDOW_MS` = 1 y), and `goLive()` (reset to
  Live · Last 24h, history cleared). `track.svelte.ts` is the **shared window fetch**
  (`/api/points` → points/truncated/status + `hoverMs` + viewport `bbox`), dedup'd by
  window — driven by the mounted TimeDock, consumed by the map trail and the density lane.
  `annotations.svelte.ts` is **axis-level** (named windows: list + jump reachable from Map
  and Trends; only the pin/polyline rendering + `pendingPan` stay map-side).
  `layers.svelte.ts` is **map-local** (data-on-map). `live.svelte.ts` is the **live
  position feed** (Drive view, below): a refcounted 1 Hz `/api/gpsd/live` poll with rAF
  interpolation between fixes and speed-gated heading (pure math in `lib/live.ts`,
  Vitest-covered).

## Map view (`/map`)

A **hybrid Svelte-native** view: the real renderers stay imperative as thin TS modules;
all chrome is idiomatic Svelte, with stores as the seam (UI intent → engine façade).

- **Engine** — `map.ts` is the single `MapView` MapLibre façade (the only module that
  touches MapLibre). `mapHost.ts` keeps the `#map` element **alive across routes** as a
  body-level singleton translated off-screen off-route (never `display:none`, which blanks
  the WebGL buffer). Basemaps + terrain DEM draping live in **`.claude/modules/basemaps.md`**.
- **TimeDock** (`TimeDock.svelte` + `TimePicker.svelte`) — the **shared** Selection-axis
  chrome (rendered by Map as the bottom overlay and by Trends above its chart — one
  interaction grammar on one axis): picker trigger, nav cluster (`◀ ⊖ ▶`, `↩` when
  history is non-empty, LIVE pill / Go-Live button), the strip, status, and the action
  row (**💾 Save window** = name the current window → a range annotation, works with zero
  points; **📍 Bookmark Here**, Live only). The picker popover is compact: preset chips +
  Live toggle commit immediately, From→To is the one staged edit, and a **Saved windows**
  section lists annotations with jump-on-click. The nav cluster is right-anchored with
  width-reserved slots (status is the flex spacer) so repeated ◀/↩ taps never chase a
  moving button. The dock drives `track.ensure(range)` — one fetch per window change.
- **TimeStrip** (`timestrip.ts`) — the kept-imperative canvas island, now **drag-to-zoom**
  (Trends semantics everywhere; no brush): drag = rubber-band → `zoomTo`; wheel = zoom
  around cursor (preview redraws per step, store commit — and the refetch — debounced
  ~200 ms); double-click/Backspace = `back()`; ←/→ shift; +/− zoom; annotation bands/
  ticks are **click-to-jump** (a 250 ms delayed-click guard lets double-click stay
  "back"). One draw pass: density-coverage fill + red stop-dwell blocks + annotation
  bands/ticks + the rubber-band. Its axis is the **requested window**, not the data
  extent, and it always renders — an empty window stays navigable. Hover emits
  `track.hoverMs` (the map's ghost dot) + tooltips.
- **Right icon rail** (Map.svelte) — replaces the old floating-panel stack: 🛰 **Data
  layers** (`DataLayers.svelte`: drone + phone + places toggles, the latter with a
  per-kind filter), 🎨 **Map style**
  (`MapStyle.svelte`: base map OSM-vector/USGS-raster + refresh, labels via `labels.ts`,
  3D terrain + exaggeration), 🚩 **Marks** (`MarksPanel.svelte`: Mark Start/End → Use
  Marks reframes the window; panel-local state), 📊 **Inspect** (`InspectPanel.svelte`:
  derived window stats from `GET /api/obd/economy` — duration/distance/speeds/fuel/MPG/
  moving/idle; fetches only while mounted = open). Exclusive-open; desktop = anchored
  card, mobile = bottom sheet; defaults closed. Below a separator, **⛶ viewport filter**:
  a mode toggle passing the map bbox into the shared `/api/points` fetch so the strip's
  density shows only time spent in view ("when was I ever at this campsite") — updates on
  moveend, suppresses refit (no fit→move→refetch loop), resets on toggle-off/route leave.
  Drone/phone/places **legends are on-map chips** under the top-left annotations
  cluster, shown only while that layer is on. Drone: `drone.ts` lazily imports
  `overlay3d.ts` (three.js tracks at MSL); phone: `phone.ts` color-by-mode breadcrumb +
  visit pins, following the window; places: `places.ts` per-kind pins —
  **viewport-driven** (moveend refetch, parks-only below z6), *not* time-windowed. The
  map's places role is **waypoints only**: pin click → `PlaceSheet.svelte`, a
  thin container over the shared `PlaceDetail.svelte`; browsing/search lives in the
  Places destination. Subsystems: **`.claude/modules/drone.md`**,
  **`.claude/modules/phone.md`**; places tier: **`.claude/modules/places.md`**.
- **Annotations** (`AnnotationsDrawer.svelte`, `AnnotationForm.svelte`, store) —
  annotations are **named windows** (pure time metadata; any tier replays against the
  bounds). Drawer (side desktop / bottom sheet mobile) is the management UI: list, ✎ edit
  / × delete, per-range fuel economy lazy-filled from `/api/obd/economy`. Jumps restore
  the window (range → exact; point → current width centred on it) and pan to the nearest
  fix. Map overlays: cyan range polylines, amber point pins; matching strip bands/ticks
  (both click-to-jump). Annotations button + **⊕ FAB** (recenter on latest raw fix) stay
  top-left. **Hover-scrub:** hovering the strip shows a ghost dot at that moment's
  position (`track.hoverMs` → binary-search nearest fix → a DOM marker).

**View behavior.** Default Live, last 24h. The map re-centers only on a fresh (non-live-
tick, non-bbox) window load or an annotation/⊕ click — otherwise free pan/zoom (browsing
≠ navigation). Returning to the Map tab does not refetch or refit (the store dedups; the
camera keeps your place). Decimation is server-side + size-aware: the client always
asks `limit=20000`.

## Drive view (`/drive`)

The "currently driving" view — the shared map engine under driving chrome, plus
the seed of navigation: a destination store + straight-line chevron (no router —
`plans/navigation-plan.md` builds the routed tier on top). Every camera/readability
knob is a named constant in `lib/follow.ts`, tuned from road feedback (zoom 17
crawl / 13 highway, ×1.4 labels as of the 2026-07-09 retune).

- **Live chain** — `/api/gpsd/live` (TPV-only gpsd snapshot; reads gpsd, not the DB) →
  `stores/live.svelte.ts` (1 Hz poll, rAF interpolation one poll-interval behind real
  time, heading speed-gated at ingest so the camera holds bearing at stoplights) →
  Drive's follow `$effect`.
- **Follow camera** — course-up, `FOLLOW_PITCH_DEG`, speed-scaled zoom with slew
  rate-limiting; all knobs are named constants in `lib/follow.ts` (pure, Vitest), tuned
  on the road. The loop `jumpTo`s per frame (the store's interpolation *is* the easing);
  the one `easeTo` is the enter transition, and per-frame sets hold off until it ends
  (a jumpTo would cancel it).
- **Gesture suspend** — user input carries `originalEvent` on `movestart`, per-frame
  jumpTo doesn't; that's how `MapView.onUserMove` tells a pan from the follow loop.
  Suspend shows a ⌖ Recenter pill; recenter re-eases in.
- **Puck** — `MapView.setPuck`: a map-rotation-aligned DOM marker (chevron), so it
  survives style swaps like the ghost dot. The Map view has no live dot to conflict
  with (its ⊕ FAB is a one-shot read).
- **Handoff contract** — the engine is a keep-alive singleton: Drive on leave clears
  the puck/breadcrumb/destination pin, unsubscribes gestures, resets label scale, and
  restores a flat/north-up camera (60° if the 3D toggle is on). The restore must be an
  **instant jump, never an ease**: Map mounts right after and its first camera command
  `stop()`s any in-flight animation, freezing the pitch mid-restore (the once-shipped
  stuck-tilted bug). Data layers are left as the user set them — Drive is the same map
  with a different camera.
- **HUD** — a bottom bar (big mph, 16-wind heading + degrees, altitude ft), read from
  the **raw** fix, not the interpolated pose (interpolation would only add display
  lag to numbers).
- **OBD strip** — a second HUD row (RPM, coolant °F, fuel %, GPH) off a 5 s
  `/api/status` poll; `fuel_rate_lph` is derived server-side into the `van` block
  (`common/obd.py` speed density). Engine-gated: `obd_link === 'online'` + rpm > 0 +
  a 30 s freshness check against the payload's own `now` (server clock, immune to
  client skew); sustained poll failure drops the snapshot client-side, since a
  frozen payload freezes its `now` too.
- **Destination chevron** — `stores/destination.svelte.ts`: a localStorage-persisted
  **value snapshot** `{name, lat, lon}` + provenance-only `source`/`sourceId`
  (place ids churn on full-replace re-imports — never dereference; the
  navigation plan attaches a route *alongside* this object, and a trip-planner
  saved place/trip stop just materializes into the same shape). Producers:
  "Navigate here" on the shared `PlaceDetail`, and a long-press/right-click
  dropped pin (`MapView.onLongPress` — hand-rolled 600 ms timer with move-slop
  cancel; pins are ephemeral, saving them belongs to the planner). HUD cell shows
  great-circle distance + a course-relative `▲` (absolute cardinal when parked —
  no course); tap clears (no confirm — recoverable). No rotation CSS transition:
  rel wraps 359→1 exactly at dead-ahead and would spin the long way.
- **Label scaling** — `MapView.setLabelScale(LABEL_SCALE)` on enter, `1` on leave:
  multiplies every vector symbol layer's `text-size` by scaling expression
  *outputs* (a zoom `interpolate` can't be wrapped in `['*',…]`), restoring
  captured originals; re-applied across style reloads. Vector base only — raster
  labels are baked pixels.
- **Wake readout** — a top-right chip (`WAKE API` / `WAKE VID` / amber `WAKE ✕`)
  polling `wakeLockStatus()` at 2 s, so on-device verification of the fallback
  video is a glance.
- **Breadcrumb** — seeded from `GET /api/points/recent` (raw tier — the processed
  tier's open segment lags the processor cursor and would stop short of the van),
  then live-extended per fresh fix via `extendCrumbs` (`lib/live.ts`): a 5 m movement
  gate (a parked van must not grow a fuzzball), 30 min age trim, and an
  oldest-half decimation at the count cap. Rendered as a puck-blue engine layer
  (`MapView.setBreadcrumb`) above the red history track, below place pins.
- **Wake lock** — `lib/wakelock.ts`: the real API needs a secure context (absent at
  `http://<LAN-IP>`), so production falls back to a NoSleep-style invisible looping
  video (~1.6 kB `web/src/assets/wakelock.mp4`, Vite-inlined, offline-safe; seek-back
  instead of `loop` — some iOS versions pause tiny looping videos).

## Other views

- **Places** (`Places.svelte`, `/places`) — the "where do we go next"
  browser over the places tier; the map keeps only waypoints. Master-detail
  (email-client) layout: a list pane (Places / Events modes, server-`q` name search
  debounced, kind chips, **Near me** ↔ **Everywhere** anchor toggle — live fix,
  distance-sorted, ~±1°) beside a detail pane rendering the shared
  `PlaceDetail.svelte` / `EventDetail.svelte` (also used by the map sheet). Desktop
  shows both panes; mobile shows one (list → detail with back). Browse state is a module
  singleton (`stores/places.svelte.ts`) so the session survives tab switches;
  "Show on map" queues `layers.pendingZoom`, enables the pins layer, and navigates —
  Map.svelte consumes the zoom once the engine is up.
- **Systems** (`Systems.svelte`) — consolidated house/van/cabin telemetry from
  `/api/sensors` + `METRIC_META` (grouped, unit-converted, per-section liveness).
  Diagnostics drill-ins (client routes): **Trends** (`Trends.svelte`, below), **gpsd**
  (`Gpsd.svelte`, `GET /api/gpsd/status`), and **ntp** (`Ntp.svelte`, `GET /api/ntp`).
- **Trends** (`Trends.svelte`, `/trends` under Systems) — the configurable trend-graph
  explorer: a registry-driven metric picker over any sensor channel (grouped by domain,
  `chart:true` columns from `/api/sensors`), overlaid on one bucketed/aligned chart
  (`GET /api/sensors/series` — contract in `.claude/modules/sensors.md`), with
  moving-average smoothing (global control + per-metric defaults from `METRIC_META`,
  e.g. `fuel_level_pct`), an optional min/max envelope band, dual-axis-by-unit, and
  localStorage presets. Renders the **shared TimeDock** above the chart (same axis +
  strip as the Map — chart drag-zoom goes through `selection.zoomTo`, double-click
  `resetZoom`; the dock's density lane shows GPS activity as context). Chart components
  live in `web/src/lib/charts/` — LayerCake composed as Svelte layers (`Trend.svelte`
  container + `Line`/`Band`/axes), dynamic-imported as its own chunk; the load-bearing
  pure logic (smoothing, series alignment, `pixelToTime` drag-zoom inversion,
  `lineSegments`) is in `charts/util.ts` under Vitest. **Sparse-data invariants:** the
  server returns a *dense* bucket grid with nulls, so lines are gap-bridged —
  `lineSegments` splits a series into runs only where a gap exceeds `gapFactor`× the
  median sample spacing (cadence-agnostic, no per-channel config), singleton runs draw
  as dots so brief engine-gated bursts stay visible, and an all-null window shows a
  "No data in this range" overlay instead of a silent blank plot. Replaces the retired
  legacy `/sensors`.
- **Docs** (`Docs.svelte`) — browses **and edits** the synced `paul-network-docs` Obsidian
  vault (`GET/PUT /api/docs/file`, `GET /api/docs/tree`). Two-pane: a collapsible file
  tree (dirs toggle, default expanded; the active doc's ancestors auto-expand so deep
  links never land hidden) + the rendered markdown. `docs.ts` is the render seam — markdown-it (raw HTML disabled, so no
  sanitizer dep), **lazy** mermaid for diagrams (its own dynamic chunk, loaded only on docs
  with a `mermaid` block), and relative-`.md` link resolution → in-app `/docs/<path>`
  navigation (the route is `prefix`-matched in `router.svelte.ts` so
  `/docs/rex/devices/foo.md` deep-links). Edit mode: `docsEditor.ts` wraps CodeMirror 6
  (**lazy** — its own chunk, loaded on the Edit click), draft preview through the same
  renderer, saves via `PUT` with the GET's content-hash `ETag` as `If-Match` (409 → the
  file changed underneath; the server auto-commits — see `api/routes/docs.py`). Edit-only
  by design: file creation/rename stays a laptop/Obsidian operation. The vault is read from
  a separate bare-repo checkout on the Pi — see CLAUDE.md Deployment.
- **Sky** (`Sky.svelte`) — the passes schedule (`/api/passes`), plus **globe**
  (`globe.ts`, lazy three.js, PC-only) and **skyplot** (`skyplot.ts`, 2D-canvas) drill-ins.
  The observatory subsystem is in **`.claude/modules/observatory.md`**.
- **Radio** (`Radio.svelte`) — Icom ID-5100A control head (freq/mode/S-meter + CTCSS +
  repeater; `/api/radio/*`), with an honest offline head when rigctld is down.

## Deferred

**Flagged follow-ups (2026-07-02 review of the time-dock rework — discuss before acting):**

- **Drone flights should follow the time window.** The drone overlay is standalone (all
  flights at once); it should scope to the Selection window like the phone layer does
  (`/api/drone/flights` already takes `start`/`end`).
- **Auto-zoom should include enabled data layers.** The map's fitBounds covers only the
  GPS trail; when drone/phone layers are on, their bounds should extend the fit so a
  window's aerial/phone data isn't off-screen.

**Longer-standing:**

- **Time-dock leftovers** — stop-dwell-block click → zoom to its dwell (the delayed-click
  guard now makes it feasible); Trends' mobile dock collapse (picker+nav only) if vertical
  space hurts; wheel commits currently push one history entry per gesture pause (make them
  replace if ↩-unwinding feels noisy); multi-source density lanes (phone/drone/OBD ticks);
  the dock on more views (shell-level).
- **Data-layers continuation** — trail color-by (speed/elevation/
  sensor channel), sensor overlays on the map, stops-as-a-layer toggle. Built onto
  `DataLayers.svelte` + the `timestrip.ts` density lane.
- **Trends continuation** — true multi-axis (>2 units) / per-series axis assignment
  beyond unit-grouping; server-side preset persistence (a small table) only if syncing
  presets across devices proves worth it. Known benign quirk: a TimePicker window change
  mid-zoom doesn't clear the zoom-out stack, so "Zoom out" can step to a pre-zoom window
  rather than the manually-picked one (every restored window is still valid; clearing on
  a foreign picker change is awkward because the live tick mutates `range`).
- **Marks continuation** — mark *types* (campsite / fuel / scenic / repair); and
  **stops → marks** — promoting a processor `kind='stop'` / `track_events` stop to a
  curated mark (deferred from the denoise work; see `.claude/modules/processor.md`). *(Window
  **energy** from Victron is the remaining Inspect stat, deferred until the
  power-integration endpoint lands.)*
- **Drive continuation** — SSE transport (replace the 1 Hz poll with a gpsd-bridged
  5 Hz stream if interpolation isn't smooth enough on the road); dark/night map style
  variant; road-name readout (query vector-tile features under the puck); parked/
  low-speed heading from the IMU compass (`plans/motion-imu-plan.md` Phase 1 — the
  v1 answer is the speed gate); annotation destinations (resolve a time range to a
  representative point — annotations have no coordinates, deferred until missed).
- Richer trail rendering (direction chevrons, head dot); per-annotation elevation/speed
  charts.
