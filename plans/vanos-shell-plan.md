# Van OS — App Shell Plan

## Context

The app started as a GPS history browser ("gps-dashboard") and has grown into a
multi-domain van computer: GPS/map history, the denoise/processor tier, drone tracks,
three sensor streams (cabin BME680, van OBD, house Victron), a GNSS observatory
(globe/passes/skyplot), radio control (ID-5100A), and infra status (gpsd/ntp). The
interface never grew to match: `/` is a privileged flagship map and everything else is a
flat scatter of standalone Jinja pages (`templates/*.html` + per-page `static/js/*.js`)
with **no shared nav chrome** — you reach `/sensors` or `/passes` by knowing the URL.

The app outgrew its single-privileged-view model. This plan reframes it as **Van OS** —
a *command panel* with persistent navigation, a glanceable status home, and the map
demoted from "the app" to one first-class destination among several.

Treat this as the durable, living plan — check items off as they land, record decisions
inline. Landed pieces fold into `.claude/modules/frontend.md` (+ the relevant `CLAUDE.md`
sections); drop this plan when the shell + ported views have landed.

---

## Confirmed decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Product identity | **Van OS** (UI name only) | The thing is a van computer, not a GPS dashboard. Rebrand the UI. |
| 2 | Repo / infra name | **Stays `gps-dashboard`** | Baked into the bare repo path, systemd units, remotes, deploy hook, GitHub. Renaming is pure churn for zero functional gain. |
| 3 | Navigation model | **Persistent shell + status home** (nav option B) | Co-equal domains need shared nav, not a privileged single view. Bottom tab bar (mobile) / sidebar (desktop). |
| 4 | Top-level tabs | **Home · Map · Systems · Sky · Radio** | Five = the phone-tab sweet spot, mapped to the user's mental model. |
| 5 | Landing surface | **Home (status glance)** | A command panel you flip between needs a glanceable overview; the app is *called* a dashboard but has none. |
| 6 | Navigation = routing | **Client-side routing (SPA shell)** | Only this keeps MapLibre/three instances alive across tab switches (felt ~1s hit per visit otherwise) and allows one long-lived WS for the live-data/alarms roadmap. An MPA structurally can't. |
| 7 | Framework | **Svelte 5 + Vite + TypeScript** | Smallest runtime, scoped styles, least ceremony, cleanest imperative-lib (`bind:this`/`onMount`) integration; closest to the project's vanilla heritage and the smallest learning surface for a solo maintainer. |
| 8 | Offline / deploy | **Build on laptop, commit `dist`** | Offline constraint is runtime-only ([[offline-constraint-interpretation]]). Deploy hook stays `uv sync`; the Pi never builds. New discipline: rebuild + commit the frontend before `git push all`. |
| 9 | URLs | **Free to change** — `/` → Home, map → `/map` | No external consumers; bookmarks are cheap to update. |
| 10 | Migration approach | **Dual-serve, port easy→hard, map last** | Freshly-deployed subsystems (observatory/radio/drone) + a mid-flight map redesign must not break mid-migration. |

### Tab → subsystem map

| Tab | Folds in | Route(s) |
|-----|----------|----------|
| **Home** | new status glance (battery, location, air, fix health, service/alarm state) | `/` |
| **Map** | history, annotations, drone overlay, live position | `/map` |
| **Systems** | Victron (house power), OBD (van), cabin env, **gpsd**, **ntp** | `/systems` (+ sub-views) |
| **Sky** | globe, passes, skyplot | `/sky` (+ sub-views) |
| **Radio** | ID-5100A control | `/radio` |

`gpsd`/`ntp` are infra diagnostics, not daily destinations — they live under Systems,
with their health echoed as cards on Home.

---

## Architecture

### Build & serve

- **`web/`** — new source tree: Svelte + Vite + TS (`web/src`, `web/vite.config.ts`,
  `web/package.json`). `node_modules` gitignored; build output to **`static/dist/`**,
  **committed**.
- **Dev:** `npm run dev` (Vite + HMR) with `/api` proxied to a locally running Flask
  (`uv run python -m api.app`). Two processes side by side.
- **Prod:** `npm run build` → commit `static/dist/` → Flask serves it. Deploy hook
  unchanged (`uv sync`); the Pi serves committed static files and never builds.
- **Flask:** add a catch-all that returns `dist/index.html` for non-`/api`, non-static,
  **migrated** SPA routes so client-side deep links resolve. `/api/*` blueprints are
  untouched. Un-migrated pages keep returning their existing Jinja templates
  (`api/app.py` currently renders `index.html` at `/` and nothing else has nav chrome).

### Vendoring flips to a cleanup

MapLibre, three, uPlot, pmtiles move from hand-copied `static/vendor/` into npm deps that
Vite bundles. Lockfile + bundle replaces manual vendoring; still fully offline at runtime
(the bundle is committed). `static/vendor/` is absorbed as each consumer ports. The
basemap assets (`static/vendor/basemap/` style/glyphs/sprite) are data, not code — they
stay served as static files.

### Persistent canvas instances

Mount the **map once** and keep it alive across routes (show/hide, not teardown). The
**globe is lazy** create/destroy (PC-only, and WebGL contexts are scarce on phones — do
not keep multiple WebGL views alive simultaneously). This is the trickiest part of the
port; prove the pattern early.

### State

A small store layer for cross-view shared state — the Selection window (so it can persist
across Map↔Systems↔Sky), and a single persistent WS feed for live status once the broker
WS transport lands (currently blocked — see `.claude/modules/sensors.md`).

**Selection is a *global time axis* (decided 2026-06-28).** Every tier shares one
`canonical_timestamp` (ms-UTC), so every historical-window read already takes `start`/`end`
(`/api/points`, `/api/sensors/:id/readings`, `/api/obd/economy`, `/api/drone/flights`,
`/api/constellation`). One app-level store — `web/src/lib/stores/selection.svelte.ts`,
`{ mode, from, to, live, brush }` — is the single window; **many consumers**, each fetching
its own data at its own resolution for that window. "What was happening *then*" across map +
sensors + OBD + drone from one pick. `live` is a *mode* of the axis (trailing → now,
auto-refresh — Home/live-dot/skyplot sit here), not an exception. **Non-consumers (honest
edges):** passes is *forward* time (a future horizon, its own control); the globe trailing
window *is* a clean consumer. **Scope:** only **Selection (time)** is global; **Layers** and
**Marks** stay map-local (drawing-on-map / curated-place concerns). So: one global axis + two
map-local axes. **Adoption is incremental** — build the store now, map is consumer #1
(Map sub-step 2), Systems/globe opt in later (small rewires each); the `TimeStrip` may later
graduate from a map widget to shell-level chrome (global picker + per-source density lane).
Don't rewire every consumer in the map pass.

---

## Phased plan

### Phase 1 — Toolchain + shell (prove the integration)
- [x] Stand up `web/` (Svelte 5 + Vite + TS), build → `static/dist/`, gitignore
  `node_modules`, commit `dist`. *(Vite 8 / Svelte 5.56 / TS 6; build = 42 kB JS / 16 kB
  gz. `vite.config.ts` base = `/static/dist/` on build, dev proxy `/api`+`/tiles` →
  `VITE_API_TARGET`.)*
- [x] Flask catch-all for SPA routes (dual-serve: only migrated routes); dev proxy config.
  *(`api/app.py`: `/` → SPA, `/map` → legacy Jinja, `/<path:path>` → SPA guarded against
  `api/`/`tiles/`/`static/`. Verified: client routes, legacy `/gpsd`/`/map`, and the API
  all resolve correctly; 392 tests green.)*
- [x] **Shell**: persistent nav (bottom tab bar mobile / sidebar desktop), active-tab
  highlighting, the five top-level destinations, "Van OS" identity. *(Tiny History-API
  router `router.svelte.ts`, no dep. Verified headless at true 390 px (CDP device
  emulation — plain `--screenshot` mis-sizes the layout viewport) + desktop.)*
- [x] **Home** (greenfield — validates new-UI ergonomics) + **`GET /api/status`**
  aggregating the headline metric per domain (latest fix + mode, Victron SOC/solar/load,
  OBD if engine recently on, cabin IAQ/temp, GNSS sat count/fix health, systemd service
  states via `common/proc.py`). *(`api/routes/status.py` — one read, each domain carries
  its `timestamp` + a server `now` so the client decides freshness; `web/src/lib/api.ts`
  typed client + Home cards/health strip with per-domain staleness, 5 s poll. Van "Off"
  is the stale-OBD path. 3 Flask-client tests; verified live against a seeded DB. Alarms
  deferred — `alarm_events` unused for now.)*
- [x] Port one trivial status page (`/ntp` or `/gpsd`) end-to-end to prove the pattern.
  *(`/ntp` ported: `status_ntp.py` refactored to `_collect()` + `GET /api/ntp` (legacy HTML
  route + `templates/ntp.html` removed); `Ntp.svelte` + typed `getNtp` + `/ntp` client
  route; Systems hub links to it via client nav. Verified: deep-link resolves through the
  catch-all, view renders banner/checks/sources/mode. 2 endpoint tests — which also cover
  the previously-untested chrony parsers.)*

**Phase 1 complete (2026-06-28).** The shell, the status home, and the migration pattern
are all proven and committed on branch `vanos-shell`. Carry-forward niceties for Phase 2:
- Parent-tab highlight: a sub-route (e.g. `/ntp`) highlights no top-level tab. Map
  sub-routes → their parent tab when Systems sub-routing is formalized.
- Alarms (`alarm_events`) not yet surfaced on Home.

### Phase 2 — Port the simple views

**Systems shape decided: hybrid** — one consolidated telemetry page (house/van/cabin)
+ diagnostics (gpsd, ntp) as drill-in sub-views.

- [x] **Systems consolidated telemetry view** — `Systems.svelte` rebuilt from `/api/sensors`
  + `METRIC_META`: house/van/cabin sections, grouped (battery/solar/dc/ac, engine/temps/
  fuel/electrical, environment), °C→°F etc. conversions, per-section liveness dot/age.
  Diagnostics drill-ins at the bottom (NTP client route, gpsd legacy link, "History &
  charts" → legacy `/sensors`). Pure helpers ported to `web/src/lib/sensors.ts`. Verified
  against live Pi data. *(Trend charts not ported — legacy `/sensors` is the drill-in for
  history; port uPlot later if Systems should own charts too.)*
- [x] **Parent-tab highlight** — `RouteDef.tab`; a sub-route (`/ntp`) lights its parent
  (Systems). Shell active = `router.current.tab === item.to`.
- [x] **gpsd** port — `_collect()` + `GET /api/gpsd/status`; `Gpsd.svelte` + `/gpsd` client
  route (tab Systems); Systems diagnostics navigate to it. Legacy route + template removed.
  2 endpoint tests.
- [x] **Sky → passes** — `Sky.svelte` is the passes schedule (horizon/mask chips, pass
  cards, 60s refresh) ported from `passes.js`, + Skyplot/Globe drill-ins. `/passes` HTML
  route + template removed; `/passes` is a client-route alias.
- [x] **Radio** port — `Radio.svelte` full control head (live readout + freq/mode/CTCSS/
  repeater writes, toast, edit-safe re-sync) from `radio.js`; `/radio` HTML route + template
  removed; now a client-route NAV tab. Renders the honest offline head when rigctld is down.
- [→] **skyplot** — DESCOPED to Phase 3 (confirmed 2026-06-28). It's an 858-line custom
  2D-canvas renderer (pointer drag), not a "simple view"; island-wrap it with globe/map (the
  heavy-canvas batch) rather than rewrite. Stays a legacy drill-in from Sky until then.

**Phase 2 deployed (2026-06-28).** Systems (consolidated telemetry) + gpsd + passes (Sky) +
radio live on the Pi. Map/Radio are no longer legacy NAV links — Radio is ported; Map is the
only NAV tab still pointing at a legacy page (`/map`), ported in Phase 3.

### Phase 3 — Port the heavy/stateful views (the heavy-canvas batch)

All three reuse their existing renderers as **islands** (mount in a Svelte shell), not
rewrites. Decide the keep-alive vs lazy policy per WebGL-context limits.

- [x] **Globe** (lazy WebGL, three.js). `globe.js` → `web/src/lib/globe.ts` as
  `mountGlobe(root) → teardown` (container-scoped; rAF stopped + GL context disposed on
  unmount; create/destroy per visit). three flips to an npm dep (pinned `0.160.1` = vendored
  r160), **dynamic-imported** so it's a separate `globe` chunk (~133 kB gz) — main bundle
  stays ~28 kB gz. `/globe` is a client route under Sky; legacy HTML route + `templates/globe.html`
  removed (`/api/constellation` untouched). `static/vendor/three/` kept until the Map port
  (overlay3d.js still uses it). Verified headless (?demo + no-data).
- [x] **skyplot** (2D canvas). `skyplot.js` → `web/src/lib/skyplot.ts` as
  `mountSkyplot(root) → teardown` (container-scoped; poll/stat timers cleared + resize
  listener dropped on unmount — no gpsd polling while off-screen). No three/heavy dep, so
  static-imported (no separate chunk). Normal flow page (narrow card), not a fixed overlay.
  `/skyplot` is a client route under Sky; Sky + the Globe panel link to it via SPA nav.
  Legacy HTML route (`status_gpsd.py`) + `templates/skyplot.html` removed (`/api/gpsd/sky`
  untouched). Verified headless (?demo, mobile width).
- [ ] **Map** (last — most complex, stateful, and in flux) with the persistent-instance
  pattern; drone overlay folds in. Still the one legacy NAV tab (`/map`).
  **Architecture DECIDED 2026-06-28: hybrid Svelte-native** (not a verbatim island-port).
  Keep *only* the real renderers imperative as thin TS modules — `map.ts` (the `MapView`
  MapLibre façade; instance persists across routes), `timestrip.ts` (canvas brush, driven
  via its `setData`/`getSelection`/`onBrush` API), `overlay3d.ts` (three drone layer). Rewrite
  all chrome (panels, drawer, annotation form, timeline labels/buttons, time picker, layer
  select) as idiomatic Svelte. A **Selection/Layers store** (`$state` module) is the seam:
  UI writes intent → an effect pushes it into `map.ts` via the façade (replaces the `app.js`
  wiring + `timeline→MapView` calls; lets the Selection window persist cross-view later).
  Keep-alive: the `#map` element is a DOM-level singleton that persists; **state** persists
  in the store; the Svelte chrome unmounts/remounts cheaply, rehydrating from the store (no
  refetch). This advances the redesign (Layers/Marks land native) instead of porting debt.
  MapLibre + pmtiles flip to npm (pinned to vendored `maplibre-gl@5.24.0` / `pmtiles@3.0.0`).
  Sub-steps (each a commit): **(1) LANDED 2026-06-28** — npm flip (`maplibre-gl@5.24.0` /
  `pmtiles@3.0.0`), `map.js`→`map.ts` façade (npm imports, typed, `setOverlay3D` seam,
  `geo.ts`), `mapHost.ts` persistent keep-alive host (body-level, translated off-screen
  off-route — never `display:none`, which blanks the WebGL buffer), `Map.svelte` minimal
  chrome (layer/3D), MapLibre dynamic-imported (Map-only chunk; main stays ~34 kB gz). Temp
  `/map-next` dev route; legacy `/map` still dual-served. Verified headless (keep-alive: one
  instance survives a client-side route round-trip with tiles intact). → **(2) LANDED
  2026-06-28** — app-global Selection store (`stores/selection.svelte.ts`: `{mode,from,to,live,
  brush}` + 30s live tick; the time axis), `timestrip.ts` (canvas brush island, scoped/typed),
  `TimePicker.svelte` + `Timeline.svelte` (picker/strip/labels/zoom-to-range orchestration
  ported from `timeline.js`); `getPoints` added to the typed API; chrome resyncs layer/3D from
  the engine on remount. Map renders the trail for `selection.range`; brush selects a
  sub-range (dwell-overlap S2). Annotation-creating bits (Create Range, Bookmark, Marks panel,
  form/drawer, strip bands) deferred to (3). Verified headless (gps_drive.db: 30d preset →
  1549-pt trail + strip, brush → sub-range count/labels/zoom-gating). → **(3) LANDED
  2026-06-28** — annotations store (`stores/annotations.svelte.ts`: list + create/edit form
  state + CRUD + `jumpTo` seam writing Selection + form-less `bookmarkCurrent` + `pendingPan`),
  `AnnotationsDrawer`/`AnnotationForm`/`MarksPanel` Svelte components, Create Range + (live-only)
  Bookmark Here in the Timeline bottom actions, and an effect that re-renders map pins/range bands
  + strip annotation ticks from the loaded points on points/list change (point-annotation jump
  pans to the nearest fix once the reframed window lands). `api.ts` gains the annotations/marks/
  obd-economy client; **marks stay map-local** (panel-owned `$state`, no store — no other
  consumer). Verified headless desktop + mobile (drawer list with per-range bounds/notes,
  jump-to-range reframes the window to 41 pts + fits, Create Range form, marks panel; only the
  expected local tile-404s in console). → **(4) LANDED 2026-06-28** — unified `Layers.svelte`
  panel (base map + labels + 3D terrain) replacing the 3 legacy floating panels (⚙ Labels /
  🏔 3D / 🚁 Drone); `lib/labels.ts` ports the POI-category/density GL-style controls
  (runtime-MapLibre-free — operates on the `gl` handed over; re-applied on every vector-style
  load via `hookLabels`, idempotent); `stores/layers.svelte.ts` is the map-local single source
  of truth (base/refresh/terrain/exaggeration + label groups/offset/minor-roads), the engine
  getting pushes via the panel's change handlers (defaults match the engine, so no read-back).
  The tile-refresh banner became an inline panel hint; **drone deferred to (5)** (its renderer
  is overlay3d). Verified headless: panel sections render, POI toggles + density + minor-streets,
  3D toggle reveals the exaggeration slider, OSM↔USGS swap loads real proxy tiles + hides
  labels + shows refresh — only expected tile-404s in console (the label *visual* effect needs
  the Pi's OSM vector archive). → **(5) LANDED 2026-06-28** — `lib/overlay3d.ts` ports the
  three.js elevated-line custom MapLibre layer (registers via `setOverlay3D` on import);
  `lib/drone.ts` controller lazily **dynamic-imports** it on first drone-enable, so three stays
  out of the map chunk — it lands in a shared `Line2` chunk with the globe (~125 kB gz, loaded
  only on demand; the map chunk holds steady at 283 kB gz). The controller fetches
  `/api/drone/flights` once + shows/clears via the façade; the **Drone section** (toggle + model
  legend + flight-count status) folds into the Layers panel; `map.ts` `showDroneTracks` ensures
  `installLayer` (idempotent) before pushing data. Verified headless: toggling drone lazy-loads
  overlay3d + three and renders the flight as a model-colored (Mini-5-Pro purple) track that
  floats at altitude and foreshortens under 3D tilt (exaggeration synced); only expected
  tile-404s in console. → **(6) LANDED 2026-06-28 (cutover)** — the SPA route flipped `/map-next`
  → `/map` (`routes.ts`); the Flask legacy `/map` route + `render_template` import removed, so
  `/map` now falls through the catch-all to the SPA. Deleted `templates/index.html` + the 11
  ported `static/js/*` (api, geo, map, labels, timepicker, timestrip, timeline, annotations,
  drone, app, overlay3d) + retired `static/vendor/three` (now npm). The deferred `⊕`
  zoom-to-current FAB folded into `Map.svelte` (`getPointsLatest` → `view.zoomTo`). Verified:
  `/map` → 200 SPA + full Map view; deleted JS + vendored three → 404; ruff/mypy/svelte-check
  clean, 399 pytest green. **The Map port is complete; the vanilla map is fully retired.**

  *Deferred (not blocking):* vendored `static/vendor/maplibre` + `static/vendor/pmtiles` are
  **kept** — the standalone dev tool `static/dev-terrain.html` still loads them via `<script>`
  (can't consume npm). Retire both when that page is updated/removed. `static/vendor/uplot` +
  `static/js/sensors.js` + `templates/sensors.html` stay until `/sensors` ports.

### Phase 4 — Reconcile & clean up
- [ ] Remove old `templates/*.html` + `static/js/*.js` as their ports land; retire
  `static/vendor/` code (keep basemap data assets).
- [ ] Resume / fold in the map-view redesign (see collision note below).
- [ ] Fold landed detail into `.claude/modules/frontend.md` + `CLAUDE.md`; drop this plan.

---

## Open decisions / flags

- **Map ⇄ map-redesign collision. DECIDED 2026-06-28: port now, finish the redesign in
  Svelte.** Port the current vanilla map into the shell as-is (Selection/`TimeStrip` axis
  done + deployed; Marks partial — Bookmark Here + edit/rename; Layers **not started**),
  then build the not-yet-started Layers axis + finish the Marks rework natively in Svelte.
  Rationale: captures the finished `TimeStrip` work, keeps "map last", and builds Layers
  exactly once (finishing vanilla first would implement Layers in vanilla then again in the
  port). The redesign's three-axis model survives intact, *inside* the Map tab; its plan
  (`plans/mapview-redesign-plan.md`) continues against the Svelte map after the port lands.
- **Home content scope.** Which metrics earn a card on the glance view, and which are
  drill-in only. Settle when building `/api/status`.
- **Testing.** Frontend currently has no JS tests (suite is pytest). Decide whether to add
  Vitest or keep verification headless/manual. The `/api/status` endpoint gets a Flask
  client test like the other read paths.

---

## Constraints carried from the project

- **Offline-first (runtime).** The committed `dist` bundle + committed basemap assets must
  render fully off-grid; no CDN at runtime. The build step (laptop, online) does not
  violate this.
- **Mobile-first.** Primary client is a phone over the van's WiFi; the shell is a bottom
  tab bar there, sidebar on desktop.
- **Read-only frontend + API.** GPS logging and the processor are untouched; this is
  `api/routes/*` reads + the new `web/` tree. `/api/status` is a new read-only aggregate.
- **Don't break deployed subsystems.** Dual-serve throughout; nothing goes dark
  mid-migration.

---

## Codebase touchpoints

- **`web/`** (new) — Svelte/Vite/TS source; shell, router, stores, views.
- **`static/dist/`** (new, committed) — build output Flask serves.
- **`api/app.py`** — SPA catch-all + dev/static wiring; `index()` route evolves.
- **`api/routes/`** — new `status.py` (`GET /api/status`); existing read blueprints
  unchanged.
- **`common/proc.py`** — systemd `is-active` for service health on Home.
- **`templates/*.html` + `static/js/*.js` + `static/vendor/` (code)** — retired as ports
  land (basemap data assets stay).
- **`.claude/modules/frontend.md` + `CLAUDE.md`** — updated as pieces land; this plan
  drops when done.
