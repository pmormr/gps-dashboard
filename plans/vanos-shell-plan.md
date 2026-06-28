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

---

## Phased plan

### Phase 1 — Toolchain + shell (prove the integration)
- [ ] Stand up `web/` (Svelte 5 + Vite + TS), build → `static/dist/`, gitignore
  `node_modules`, commit `dist`.
- [ ] Flask catch-all for SPA routes (dual-serve: only migrated routes); dev proxy config.
- [ ] **Shell**: persistent nav (bottom tab bar mobile / sidebar desktop), active-tab
  highlighting, the five top-level destinations, "Van OS" identity.
- [ ] **Home** (greenfield — validates new-UI ergonomics) + **`GET /api/status`**
  aggregating the headline metric per domain (latest fix + mode, Victron SOC/solar/load,
  OBD if engine recently on, cabin IAQ/temp, GNSS sat count/fix health, systemd service
  states via `common/proc.py`, any alarms).
- [ ] Port one trivial status page (`/ntp` or `/gpsd`) end-to-end to prove the pattern.

### Phase 2 — Port the simple views
- [ ] Systems shell + the remaining status/telemetry pages: sensors, OBD, Victron, gpsd,
  ntp into one **Systems** domain (note: `/sensors` reads the sensor registry, but OBD
  lives in `obd_readings` / `/api/obd/*` and Victron in `victron_readings` — unifying
  them into one Systems view is partly new work, not just a move).
- [ ] **Sky** shell: passes, skyplot. (Globe handled with Map in Phase 3 — both are heavy
  canvas/WebGL.)
- [ ] **Radio** page port.

### Phase 3 — Port the heavy/stateful views
- [ ] **Map** (last — most complex, stateful, and in flux) with the persistent-instance
  pattern; drone overlay folds in.
- [ ] **Globe** (lazy WebGL).

### Phase 4 — Reconcile & clean up
- [ ] Remove old `templates/*.html` + `static/js/*.js` as their ports land; retire
  `static/vendor/` code (keep basemap data assets).
- [ ] Resume / fold in the map-view redesign (see collision note below).
- [ ] Fold landed detail into `.claude/modules/frontend.md` + `CLAUDE.md`; drop this plan.

---

## Open decisions / flags

- **Map ⇄ map-redesign collision.** `plans/mapview-redesign-plan.md` ([[next-ui-pass]])
  is being built on the *vanilla* map (Selection axis done; Layers + Marks rework
  pending). When we reach Phase 3 we must choose: finish the vanilla redesign then port a
  finished thing, **or** fold the remaining redesign (Layers/Marks) into the Svelte port.
  Not deciding now — flagged so it can't ambush us. The redesign's three-axis model
  (Selection/Layers/Marks) survives the port intact; it's *inside* the Map tab.
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
