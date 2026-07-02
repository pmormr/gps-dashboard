# Frontend

**Van OS** — a client-side SPA (Svelte 5 + Vite + TypeScript) in `web/`, built to
`static/dist/` (committed) and served by Flask. A persistent nav shell with six
top-level destinations (**Home · Map · Systems · Docs · Sky · Radio**); the map is one
tab among several, not the privileged single view it once was. Mobile-first (the primary
client is a phone over the van's WiFi): a bottom tab bar on phones, a left sidebar on
desktop.

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
  Heavy deps are **dynamic-imported** so each heavy view is its own chunk and the main
  bundle stays small (~44 kB gz): `map` (MapLibre, ~283 kB gz), `globe` + `overlay3d` share
  a `three` chunk (~125 kB gz, lazy), and the Trends chart is its own ~10 kB gz LayerCake
  chunk. The basemap **data** assets (`static/vendor/basemap/` style/glyphs/sprite) stay
  served as static files. `static/vendor/{maplibre,pmtiles}` are retained only for the
  standalone `static/dev-terrain.html` dev tool.

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
  `layers.svelte.ts` is **map-local** (data-on-map).

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
  layers** (`DataLayers.svelte`: drone + phone toggles), 🎨 **Map style**
  (`MapStyle.svelte`: base map OSM-vector/USGS-raster + refresh, labels via `labels.ts`,
  3D terrain + exaggeration), 🚩 **Marks** (`MarksPanel.svelte`: Mark Start/End → Use
  Marks reframes the window; panel-local state), 📊 **Inspect** (`InspectPanel.svelte`:
  derived window stats from `GET /api/obd/economy` — duration/distance/speeds/fuel/MPG/
  moving/idle; fetches only while mounted = open). Exclusive-open; desktop = anchored
  card, mobile = bottom sheet; defaults closed. Below a separator, **⛶ viewport filter**:
  a mode toggle passing the map bbox into the shared `/api/points` fetch so the strip's
  density shows only time spent in view ("when was I ever at this campsite") — updates on
  moveend, suppresses refit (no fit→move→refetch loop), resets on toggle-off/route leave.
  Drone/phone **legends are on-map chips** under the top-left annotations cluster, shown
  only while that layer is on. Drone: `drone.ts` lazily imports `overlay3d.ts` (three.js
  tracks at MSL); phone: `phone.ts` color-by-mode breadcrumb + visit pins, following the
  window. Subsystems: **`.claude/modules/drone.md`**, **`.claude/modules/phone.md`**.
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

## Other views

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
- **Docs** (`Docs.svelte`) — browses the synced `paul-network-docs` Obsidian vault
  (`GET /api/docs/{tree,file}`). Two-pane: a file tree + the rendered markdown. `docs.ts`
  is the render seam — markdown-it (raw HTML disabled, so no sanitizer dep), **lazy**
  mermaid for diagrams (its own dynamic chunk, loaded only on docs with a `mermaid` block),
  and relative-`.md` link resolution → in-app `/docs/<path>` navigation (the route is
  `prefix`-matched in `router.svelte.ts` so `/docs/devices/foo.md` deep-links). The vault is
  read from a separate bare-repo checkout on the Pi — see CLAUDE.md Deployment.
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
- **Vendored `static/vendor/{maplibre,pmtiles}` retirement** — blocked on the standalone
  `static/dev-terrain.html` dev tool (script-tag loads, can't use npm); retire with it.
- Richer trail rendering (direction chevrons, head dot); per-annotation elevation/speed
  charts.
