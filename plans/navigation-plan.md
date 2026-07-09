# Navigation Plan

**Status: LOCKED 2026-07-09 — all decisions resolved, ready to execute.** This
doc is self-contained for a fresh session; check items off as they land, record
findings inline.

## Context

Offline **routing** for the map/drive views: pick a destination, get a road route
with ETA and distance-remaining, follow it on the Drive view, re-route when off
course. The engine + a North America graph live on the NVMe; nothing touches the
WAN at runtime.

Turn-by-turn maneuver guidance (banner/voice) is explicitly **later** — but the
engine returns maneuvers with every route, so v1 keeps them in the payload and
simply doesn't render them. Nothing about v1 forecloses tier 3.

Builds directly on the Drive view (`.claude/modules/frontend.md` § Drive view —
plan landed and folded 2026-07-09): its destination store ("Navigate here" from
attractions, dropped pins) is the entry point navigation extends. The store is a
**value snapshot** `{name, lat, lon}` + provenance-only source ids — routes
attach *alongside* it, never inside. **Prereq met: Drive is fully built**; only
its road-verification drive is outstanding.

Treat this doc as the durable, living plan — check items off as they land, record
decisions inline.

---

## Confirmed decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Engine | **Valhalla** | Tiled, disk-backed graph — serving RAM is a few hundred MB regardless of extent, which is what a 4 GB CM5 needs. Returns maneuvers natively; map-matching + isochrones come free for later. OSRM eliminated (whole graph in RAM — NA is far beyond 4 GB); GraphHopper is the fallback (mmap-able but drags a JVM); BRouter is bike-oriented. |
| 2 | v1 scope | **Route line + ETA + off-route re-route** | Turn-by-turn deferred. All the risk (engine on the Pi, graph build) is in this tier; maneuvers are a rendering increment later. |
| 3 | Graph extent | **Full North America** — the Geofabrik `north-america` extract only (Canada/Greenland/Mexico/US) | Matches the basemap philosophy (33 GB OSM, 105 GB terrain). Disk is a non-issue (739 GB free). A **Colorado build validates the pipeline first** — measures build RAM/time and route quality cheaply before committing to the NA build. **Accepted gap (2026-07-09):** the basemap/POI footprint (`-168,7,-52,72`, per `plans/attractions-poi-plan.md`) also covers Central America — routing deliberately does not; primary concern is the USA. A Central America place may be searchable/rendered but not routable. `valhalla_build_tiles` takes multiple PBFs if this is ever revisited. |
| 4 | Build host | **NAS** (Mac permitted) | rex-nas is an i5-1235U with **32 GB RAM** — comfortable for the NA graph build — and its gigabit link beats the Mac's WiFi for the ~15 GB PBF download; it already builds containers (exiftool precedent). Same playbook as the terrain archive: build off-Pi, rsync → `.tmp`, atomic `mv`. |
| 5 | Costing | **`auto` with `height: 2.75`** (m), height only | The van is just under 9 ft (2.74 m); 2.75 gives margin in the safe direction. Valhalla's auto costing accepts `height`/`width` directly (upstream PR #3179) — no need for `truck` costing, which drags truck-specific road restrictions that don't apply to a van. Width (~2.5 m with mirrors) deferred: OSM `maxwidth` coverage is sparse, marginal effect, slight over-caution risk — add if Phase 0 routes look wrong. |
| 6 | Runtime shape | **pyvalhalla in-process** (`Actor` inside Flask) + **Python 3.12 bump** | A pip dep in `uv.lock` (offline-installable like everything else), maintained by Valhalla's own people (3.8.2 released 2026-07-08, aarch64 abi3 wheels), needing **no** new systemd unit, deploy-hook block, or source build. Cost accepted: wheels are cp312-abi3 → uv-managed Python 3.12+ on the Pi (one-time online download, cached; system Python is 3.11.2) and a project-wide `requires-python` + `[tool.mypy] python_version` bump. Phase 0 validates pyvalhalla against the Colorado extract before the Pi commitment; **recorded fallback** if it fails: `valhalla_service` daemon on `127.0.0.1:8002` (source build on Pi, enabled-gated unit + hook block, Flask HTTP proxy). |
| 7 | Actor concurrency | **Module-level `Actor` behind a `threading.Lock`**, lazy-init on first request | The Actor isn't thread-safe; a single-user LAN with occasional route requests loses nothing to serialization. Lazy init means a missing/bad extract degrades to a clean API error (503), not an app-startup failure. |
| 8 | Polyline decode | **Client-side** (Valhalla polyline6, ~30-line pure-TS decoder) | Smaller payloads; the maneuvers' `begin_shape_index`/`end_shape_index` reference the encoded shape, so decoding client-side keeps them aligned for the deferred turn-by-turn phase. Pure function → Vitest. |

---

## Open decisions

None — all resolved 2026-07-09 (see confirmed decisions 1–8).

---

## Traps (identified up front)

1. **Python floor.** pyvalhalla wheels are cp312-abi3; the Pi runs system Python
   3.11.2. Going in-process means `uv python install 3.12+` on the Pi (online,
   one-time, cached — the same offline-constraint bucket as `libhamlib-utils`)
   and bumping `requires-python` + `[tool.mypy] python_version` project-wide.
2. **The NA graph build is the RAM-heavy step**, not serving. `valhalla_build_tiles`
   on a ~15 GB PBF wants tens of GB; the NAS's 32 GB should cover it, and the
   Colorado build (Phase 0) measures the real footprint before the NA attempt.
   Knobs if tight: `mjolnir.concurrency`, staged builds (`--start`/`--end`
   stages), swap.
3. **Ship a tile extract (tar), not a tiles directory.** `valhalla_build_extract`
   packs the graph into one indexed `.tar` that Valhalla mmaps — one file to
   rsync + atomic-replace (the terrain playbook), and the low-RAM serving story
   depends on it.
4. **Admin + timezone DBs are build-time inputs** (`valhalla_build_admins`,
   `valhalla_build_timezones`). Skip them and turn restrictions/crossing logic
   silently degrade — bake them into the build script from the start.
5. **Don't trust the height gate as a clearance guarantee.** OSM `maxheight`
   coverage is incomplete; the router avoids *mapped* low clearances only. The
   HUD should never imply otherwise.
6. **Re-route hysteresis.** Off-route detection from the live store must gate on
   (a) sustained deviation (e.g. >50 m cross-track for >10 s), (b) moving — GPS
   noise while parked must not trigger re-route churn.
7. **Graph currency.** The graph is a snapshot of OSM at build time; rebuild
   on-grid occasionally, same cadence philosophy as the basemap. Not a treadmill.
8. **Only if the recorded fallback (`valhalla_service`, decision 6) is ever
   taken:** the deploy hook needs its per-unit restart block added on the Pi
   (known trap — new services are not auto-covered). The chosen pyvalhalla path
   needs no hook change.

---

## Constraints carried from the project

- **Offline-first.** Engine, graph, and every route computation are local.
  pyvalhalla (if chosen) rides `uv.lock`; graph builds are on-grid prep, like
  tile precaching.
- **GPS logging is sacred.** Routing reads the live store; the logger's write
  path is untouched.
- **Committed SPA build.** Route overlay/HUD ride the normal `web/` build-commit
  flow; the Pi never builds.
- **The Pi never builds the graph.** NA builds happen on the NAS/Mac; the Pi
  receives one tar.

---

## Architecture

```
  destination store (built — frontend.md § Drive view)
       │  "route to this"
       ▼
  POST /api/route ──── Flask ──── pyvalhalla Actor (mmap'd NA extract tar;
       │                           module-level, lock-guarded, lazy-init)
       ▼
  route store (web/src/lib/stores/route.svelte.ts)
   ├─ route line overlay (MapView — Drive + Map)
   ├─ HUD: ETA · distance remaining        (maneuvers kept in payload, unrendered)
   └─ off-route monitor (cross-track vs live store, hysteresis) ──▶ re-route
```

Graph pipeline (on-grid, off-Pi):

```
  Geofabrik PBF ──▶ docker valhalla: build_admins + build_timezones + build_tiles
                └──▶ valhalla_build_extract ──▶ valhalla-na.tar
                        └──▶ rsync → /mnt/nvme/tiles/valhalla-na.tar.tmp → mv
```

---

## Phases

Each phase independently shippable; Phase 0 is disposable validation.

- **Phase 0 — Colorado pipeline validation (off-Pi).** Docker Valhalla on the
  NAS (or Mac): `colorado-latest.osm.pbf` → admins/timezones/tiles → extract
  tar. Route it locally via pyvalhalla (macOS arm64 wheel) against drives we
  know; sanity-check the height gate against a mapped low clearance. Record:
  build RAM/time, tar size, route quality. **Validates the pyvalhalla bet
  (decision 6)** — only a failure here reopens the `valhalla_service` fallback.
- **Phase 1 — NA graph build + ship.** NA PBF downloaded on the NAS (gigabit);
  same build script at continental scale; `valhalla-na.tar` → NVMe atomic
  swap. Write the build as a documented script (`tools/` or NAS-side) so the
  rebuild cadence is a command, not archaeology. **PBF reuse:** if
  `plans/attractions-poi-plan.md` Phase 1 already downloaded the Geofabrik
  `north-america` PBF to the NAS, build from that file — one download, and
  graph + POI DB share an OSM snapshot (no searchable-but-not-routable skew).
  Same courtesy in reverse.
- **Phase 2 — Pi runtime + `/api/route`.** uv-managed Python 3.12 on the Pi +
  `requires-python`/mypy bump + pyvalhalla dep (decision 6); module-level
  lock-guarded lazy `Actor` (decision 7). `POST /api/route` (origin, dest,
  costing auto + height) → encoded shape + summary (+ maneuvers, passthrough;
  client decodes — decision 8). Config
  via env (`GPS_VALHALLA_EXTRACT_PATH`, unit-file env var like the terrain
  path). Tests: a tiny committed fixture extract (a few km² of Colorado —
  measure size; mock if it's not small) driving the route read path.
- **Phase 3 — Frontend routing UX.** Route request from the Drive destination
  flow (extends the built chevron: chevron = no route yet / fallback); route
  line overlay on the shared MapView; HUD gains ETA + distance remaining;
  off-route monitor with hysteresis → auto re-route from current position.
  Clear-route affordance; route survives reloads alongside the destination.

---

## Deferred pile (flagged, not scoped)

- **Turn-by-turn HUD** — maneuver banner from the already-carried maneuvers;
  voice via browser SpeechSynthesis (verify offline voices on the phone).
- **Offline destination search** — likely superseded by
  `plans/attractions-poi-plan.md` (OSM extract → FTS5-searchable attractions
  tier), which strictly covers what this bullet proposed: destination search =
  search attractions → "Navigate here" → destination store, no nav-owned
  geocoder. **Re-evaluate against that plan's state before scoping anything
  here** — build this only if a gap remains (e.g. addresses, which the POI
  tier won't carry).
- **Map-matching (Meili)** — snap GPS trails to roads; possible processor synergy.
- **Isochrones** — "how far can I get in 2 h" overlay; engine supports it free.
- **Elevation-aware costing** — Valhalla can ingest elevation for grade; skip
  unless mountain routing quality demands it (wants raw SRTM, not our PMTiles).
- **Graph refresh automation** — scripted seasonal rebuild, like attractions
  re-import cadence.

---

## Codebase touchpoints (anticipated)

- **`api/routes/nav.py`** — `POST /api/route` (+ maybe `GET /api/route/status`
  for extract presence/version in the Systems view).
- **`pyproject.toml`** — pyvalhalla dep + `requires-python`/mypy bump
  (decision 6).
- **`deploy/gps-dashboard.service`** — extract-path env var (fallback path only:
  a new `valhalla.service` + hook block).
- **`web/src/lib/stores/route.svelte.ts`** — route state (shape, summary,
  maneuvers), off-route logic feeding from the live store.
- **`web/src/lib/map.ts`** — route line layer (like drone/phone overlays).
- **`web/src/views/Drive.svelte`** — ETA/distance HUD, re-route wiring.
- **Build script** — graph build + extract + ship (docker-side; documented).
- **Tests** — route endpoint (fixture extract or mock), cross-track/hysteresis
  math (pure TS → Vitest), polyline decoder.
