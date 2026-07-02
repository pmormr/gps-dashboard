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
- **Stores** (`web/src/lib/stores/`) — `selection.svelte.ts` is the **global time axis**
  (`{mode,from,to,live,brush}` + a 30 s live tick): every historical-window read takes the
  same canonical-ms-UTC `start`/`end`, so one window can drive many consumers (the map is
  consumer #1; Systems/globe can opt in later). `annotations.svelte.ts` and
  `layers.svelte.ts` are **map-local** (curated places / data-on-map) — not global.

## Map view (`/map`)

A **hybrid Svelte-native** view: the real renderers stay imperative as thin TS modules;
all chrome is idiomatic Svelte, with stores as the seam (UI intent → engine façade).

- **Engine** — `map.ts` is the single `MapView` MapLibre façade (the only module that
  touches MapLibre). `mapHost.ts` keeps the `#map` element **alive across routes** as a
  body-level singleton translated off-screen off-route (never `display:none`, which blanks
  the WebGL buffer). Basemaps + terrain DEM draping live in **`.claude/modules/basemaps.md`**.
- **Timeline** (`Timeline.svelte` + `TimePicker.svelte`) — the Selection-axis chrome. The
  Graylog-style picker (Last / Around / From→To, preset chips, Live) writes the `selection`
  store; an effect refetches `/api/points` for the window. The sub-range **`timestrip.ts`**
  is a kept-imperative canvas island (one draw pass: density-coverage fill + red stop-dwell
  blocks + annotation bands/ticks + dim mask + two-handle brush; pointer/touch drag·pan·tap
  + keyboard; hover tooltips). Its axis is the **requested window**, not the data extent.
  **Zoom to Range** promotes the brush to a tighter fetch window (more detail —
  `/api/points` is size-aware decimated). Stops select by dwell-interval overlap.
- **Annotations** (`AnnotationsDrawer.svelte`, `AnnotationForm.svelte`, store) — drawer
  (side desktop / bottom sheet mobile) lists points + ranges; click jumps the global window
  (range → `range`, point → `around`) and pans to the nearest fix; ✎ edit / × delete; per-
  range fuel economy lazy-filled from `/api/obd/economy`. Map overlays: cyan range
  polylines, amber point pins, constant-size red stop dots; matching strip bands/ticks.
  **Create Range** (brush ≥2 pts) and **📍 Bookmark Here** (Live only, latest fix) create
  annotations. **⊕ FAB** recenters on the latest raw fix.
- **Marks** (`MarksPanel.svelte`) — persisted live range-construction (Mark Start/End →
  Use Marks reframes the window). Panel-local state (no store — no other consumer).
- **Inspect window** (`InspectPanel.svelte`) — a collapsible chrome panel of derived
  stats for the *current Selection window* (duration, GPS distance, avg-moving/max speed,
  fuel + MPG, moving/idle time), from `GET /api/obd/economy`. Generalizes the per-saved-
  range fuel-economy readout (AnnotationsDrawer) to any scrubbed window; fetches only while
  open. Shows GPS distance even with no OBD coverage.
- **Layers** (`Layers.svelte` + store, `labels.ts`) — one panel folding in base map
  (OSM vector / USGS raster + refresh), **labels** (POI categories / density / minor
  streets, vector-only — `labels.ts` drives the Protomaps GL style), **3D terrain**
  (toggle + exaggeration), **drone** (toggle + legend; `drone.ts` lazily imports
  `overlay3d.ts` — a three.js custom MapLibre layer floating tracks at MSL altitude), and
  **phone history** (toggle + mode legend; `phone.ts` color-by-mode breadcrumb + visit pins,
  refetched on the global time selection while on). The drone subsystem is in
  **`.claude/modules/drone.md`**; phone in **`.claude/modules/phone.md`**.

**View behavior.** Default Live, last 24h. The map re-centers only when Live is on or an
annotation/⊕ is clicked — otherwise free pan/zoom (browsing ≠ navigation). Decimation is
server-side + size-aware (C17): the client always asks `limit=20000`.

## Other views

- **Systems** (`Systems.svelte`) — consolidated house/van/cabin telemetry from
  `/api/sensors` + `METRIC_META` (grouped, unit-converted, per-section liveness).
  Diagnostics drill-ins (client routes): **Trends** (`Trends.svelte`, below), **gpsd**
  (`Gpsd.svelte`, `GET /api/gpsd/status`), and **ntp** (`Ntp.svelte`, `GET /api/ntp`).
- **Trends** (`Trends.svelte`, `/trends` under Systems) — the configurable trend-graph
  explorer: a registry-driven metric picker over any sensor channel, overlaid on one
  bucketed/aligned chart (`GET /api/sensors/series`), scoped by the global Selection axis
  (drag-to-zoom drives `setRange`), with smoothing, a min/max band, and localStorage
  presets. LayerCake chart, dynamic-imported as its own chunk. Replaces the retired legacy
  `/sensors`. Detail in **`plans/sensor-graphing-plan.md`**.
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

- **Layers (Axis 2) continuation** — trail color-by (speed/elevation/sensor channel),
  sensor overlays on the map, stops-as-a-layer toggle. Built onto the `Layers.svelte` panel
  + the `timestrip.ts` density lane (the multi-channel handoff S4 left open). *(The
  Selection-synced chart is done — the Trends view; the divorced legacy `/sensors` +
  vendored uPlot were retired.)*
- **Marks (Axis 3) continuation** — mark *types* (campsite / fuel / scenic / repair); and
  **stops → marks** — promoting a processor `kind='stop'` / `track_events` stop to a
  curated mark (denoise **Phase 6**, see `.claude/modules/processor.md`). *(The
  "inspect this window" panel — generalizing the per-range fuel-economy readout to any
  Selection — is built: `InspectPanel.svelte`, above. Window **energy** from Victron is
  the remaining stat, deferred until the power-integration endpoint lands.)*
- **Vendored `static/vendor/{maplibre,pmtiles}` retirement** — blocked on the standalone
  `static/dev-terrain.html` dev tool (script-tag loads, can't use npm); retire with it.
- Richer trail rendering (direction chevrons, head dot); per-annotation elevation/speed
  charts; the Selection axis graduating to shell-level chrome (global picker + per-source
  density lanes).
