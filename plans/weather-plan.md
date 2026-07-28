# Weather subsystem plan (radar first)

**Status:** shaped 2026-07-28, not started. Radar is layer one; the subsystem is
designed to grow other NWS/NOAA weather layers behind one registry.

**Goal:** a **Weather** tab — a MapLibre map (same basemap/look as Map & Drive)
showing an animated, scrubbable **national radar mosaic** you can zoom to your
area. Backed by a *rolling 2-week local archive* of frames the van captures
opportunistically while online and plays back offline.

---

## The shape: capture-while-online, play-offline

Radar is the one map layer that can't work from nothing off-grid — a frame is
worthless 20 min after capture, so there is nothing to pre-cache before a trip.
The design instead builds a **local archive by continuous capture while
connected**, and plays it back off-grid. Same tier-shape as drone media / radio
audio: ephemeral, rebuild-worthless, **outside the backup path**.

**Hard constraint from the source (confirmed):** NWS's live service holds only a
**~2–4 hour moving window** upstream. So:
- "Catch up on the next run" = grab the handful of frames from the last ~2–4 h we
  don't already have. It is **not** a way to backfill a day we were dark.
- Archive completeness == our connectivity over the trailing 2 weeks. A long
  off-grid gap is a permanent hole in the loop. (User-accepted: missing frames
  are fine.)
- This is honest framing to carry into CLAUDE.md: a deliberate "capture while
  online, play offline" tier, **not** a violation of the Offline Constraint (the
  core stays offline; only *filling* the archive needs a link, and it degrades to
  a clean no-op when there isn't one).

---

## Data source (confirmed, no key, public domain — no attribution required)

NWS **`radar_base_reflectivity_time`** ImageServer (MRMS 1 km quality-controlled
base reflectivity), the documented machine backend behind radar.weather.gov (the
website itself moved to a tile renderer; we target the GIS service, never scrape
the site).

- Base URL: `https://mapservices.weather.noaa.gov/eventdriven/rest/services/radar/radar_base_reflectivity_time/ImageServer`
- Also exposes **OGC WMS 1.3.0** at `.../ImageServer/WMSServer` — GetMap with
  `BBOX`, `WIDTH/HEIGHT`, `TIME`, `TRANSPARENT=true`, `FORMAT=image/png`. WMS
  GetMap is the intended fetch mechanism (standard, transparent PNG); ArcGIS REST
  `exportImage` is the fallback.
- **Web Mercator** (WKID 102100) — matches our tile grid, no reprojection.
- Time-enabled: `TIME` in epoch-ms UTC (WMS: ISO8601). Omit → latest. The live
  `?f=json` reports the current `timeExtent` — that's how we enumerate available
  frames per run.
- Coverage: CONUS + AK/HI/Caribbean/Guam. A single CONUS bbox export excludes
  AK/HI — fine for a lower-48 van; note it.
- Refresh cadence upstream: ~5–10 min.

GeoServer alternative (kept as a note, not the primary): `conus_bref_qcd` WMS at
`https://opengeo.ncep.noaa.gov/geoserver/conus/conus_bref_qcd/ows`.

---

## Architecture

Three parts, each mapped to an existing pattern in the repo.

### 1. Fetcher — timer-driven oneshot (the `gps-db-backup` / `gps-drone-sync` shape)

- New `radar/` package (pure helpers: bbox-grid math, mosaic, tile slicing,
  retention selection — clockless + table-testable, mirroring `radio/`), plus a
  thin `tools/fetch_radar.py` CLI, driven by `deploy/radar-fetch.timer` +
  `radar-fetch.service` (`Type=oneshot`, `After=network-online.target`).
- **Timer every ~5 min**; capture at the native **~10-min cadence** (dedup so a
  5-min tick that finds no new upstream frame is a no-op).
- Per run:
  1. Reachability check (GET `?f=json`, short timeout). Offline → log + exit 0
     (clean no-op, like `ssh_reachable` in `backup_db`).
  2. Read `timeExtent` → the frame timestamps available in the upstream window.
  3. Diff against what's on disk; for each missing frame fetch the **grid of
     bbox tiles** at high res (see Delivery), mosaic to one Web-Mercator master,
     slice to tiles, pack to a per-frame archive.
  4. Prune frames older than **14 days**.
- Must handle `KeyboardInterrupt` → `"\nInterrupted."`, exit 130 (tools rule).

### 2. Storage + delivery — tile the radar, don't ship a big image

The delivery cost is the governing constraint, not storage (NVMe has room; <50 GB
is negligible). A single CONUS PNG per frame means the browser downloads the
whole national image even when zoomed into one state, and decodes a multi-MB
image per animation step on a phone. So we **tile the radar like a slippy layer**
(the `{z}/{x}/{y}` model of the USGS/OSM layers): the browser pulls only the
tiles in the current viewport at the current zoom, and high capture resolution
stops fighting cheap delivery.

- **Best resolution via a bbox grid:** ArcGIS caps a single export (~4096 px), so
  for ~1 km detail across CONUS the fetcher requests a **grid of bboxes per frame
  and mosaics them** — the answer to "can we get best resolution for the whole
  map": yes, by querying multiple bounding boxes.
- **Per-frame PMTiles (recommended format):** slice each master into an image
  pyramid (transparent PNG tiles, sparse — skip empty tiles) packed into **one
  PMTiles file per frame**. Reuses the exact `osm.pmtiles` serving path
  (`send_file(..., conditional=True)` byte-range) — the browser range-reads only
  the tiles it needs, one file per frame avoids inode blow-up, and MapLibre reads
  it via the already-registered `pmtiles://` protocol.
  - Zoom range ~z2–z8 (MRMS 1 km ≈ z8 native); overzoom beyond.
- Route: `GET /tiles/radar/<frame_ts>.pmtiles` (range-served, mirrors
  `osm_pmtiles`). Plus `GET /api/radar/frames?window=<hours>` → JSON list of
  available frame timestamps (+ coverage/age) the frontend builds the timeline
  and pmtiles URLs from. Optional `GET /api/radar/status` (archive span, last
  capture, gaps) for a Diagnostics/Data drill-in.
- Estimated footprint: 14 d × 6/h × 24 = ~2016 frames; sparse radar PMTiles ~
  a few MB each → order ~5–15 GB. Measure and tune; well under 50 GB.

### 3. View — new Weather tab (MapLibre, consistent basemap)

- A twelfth/next nav destination `/weather`, MapLibre over the shared OSM basemap
  (user wants the look consistent with Map & Drive). Reuse the map engine /
  `mapHost` keep-alive where practical.
- **Animation = the standard MapLibre radar-loop technique:** preload the current
  playback *window* (e.g. last 1–3 h = ~6–18 frames) as raster sources from their
  per-frame PMTiles and toggle layer visibility (`setLayoutProperty(..,
  'visibility', ..)`) frame to frame. **Do not** load all 2016 frames — the
  2-week archive is for *scrubbing to any past time*; the animated loop is a
  sliding window over it.
- Controls: play/pause, speed, opacity, a scrubber across the archive with a
  timestamp label, and a "center on van" control (national default zoom ↔ nearby).
- **Keep radar's clock fully separate from the GPS history timestrip** — radar is
  real-time-anchored; coupling it to the selection/zoom-history axis would be a
  mess.
- A layer toggle in the view is the seam for the future weather layers below.

---

## Locked decisions

1. **Retention:** 14 days (storage is negligible; the ceiling is delivery, solved
   by tiling).
2. **Render:** MapLibre raster over the shared OSM basemap (consistent look), a
   *separate view*, not a toggle on the main map.
3. **Best resolution:** yes — bbox-grid mosaic per frame beats the single-export
   cap.
4. **Delivery:** tiled (per-frame PMTiles), so the browser only pulls the visible
   viewport. This is what makes "best resolution" and "cheap to render" compatible.
5. **Cadence:** capture ~10-min frames; timer fires ~5 min.
6. **Source:** NWS `radar_base_reflectivity_time` via WMS GetMap.

---

## Extensibility: a weather-layer registry (radar is layer one)

Design the fetcher + routes around a **layer registry** (a `radar/`→`weather/`
registry entry per layer: id, source URL/service, bbox strategy, cadence,
raster-vs-vector, observed-vs-forecast, retention), so a new weather layer is a
registry entry, not new plumbing. Two pipelines:

- **Raster (tiled)** — reuses capture→mosaic→tile→PMTiles wholesale.
- **Vector (polygons)** — much lighter: fetch GeoJSON, store, render as a
  MapLibre fill/line. Tiny storage.

And two time models:
- **Observed/nowcast** — trailing window, fits the 2-week rolling-past archive.
- **Forecast** — issued-at → valid-at (future); keep only the latest issuance,
  its own time handling. Don't force it into the past-archive model.

### Candidate layers (phased; endpoints confirmed at build time)

Ranked by van usefulness × pipeline fit:

1. **Watches / Warnings / Advisories polygons** — *vector, observed.* Highest
   value-per-byte and safety-relevant (tornado / flash-flood / winter-storm boxes
   over your position). Tiny GeoJSON. Strong candidate to build right after radar.
2. **GOES satellite (IR / visible cloud)** — *raster, observed.* Same tile
   pipeline as radar; the whole-cloud picture beyond precip. ~5–10 min updates.
3. **SPC convective outlooks** — *vector, forecast.* Day 1–3 severe-weather risk
   polygons. Trip-planning; tiny.
4. **NDFD forecast grids** — *raster, forecast.* Wind/gust, temp, precip
   probability. Directly useful for hillclimb/event days (wind on the mountain).
   Forecast time-model → a later phase.

---

## Phases (action items — walk one at a time)

- **P0 — Source spike.** Confirm WMS GetMap params against the live service:
  transparent PNG output, `TIME` handling, `exportImage`/GetMap size caps, the
  real upstream window depth, and the bbox-grid/mosaic seams (no visible tile
  edges). Nail the CONUS bbox + target resolution. *(One throwaway script.)*
- **P1 — Fetcher + storage.** `radar/` pure helpers (grid/mosaic/tile/retention)
  + `tools/fetch_radar.py` + timer/service units. Per-frame PMTiles on the NVMe,
  14-day prune. Tests on the pure helpers.
- **P2 — API + delivery.** `/tiles/radar/<frame>.pmtiles` (range) +
  `/api/radar/frames` (+ maybe `/api/radar/status`). Flask-client tests.
- **P3 — Weather view.** `/weather` tab, MapLibre over OSM, windowed frame
  animation (visibility toggle), scrubber/play/opacity/center-on-van.
- **P4 — Warnings layer** (vector) — the first proof the registry generalizes.
- **P5+ — GOES / SPC / NDFD** as separate phases.

Build + commit `static/dist/` before pushing (Pi never builds). Add the
`radar-fetch` restart/enable branch to the deploy hook (new service needs its
block added on the Pi). Enabled-gated until wired, like the sensor readers.

---

## Open questions to lock during build

- **P0 output:** exact CONUS bbox, grid dimensions, per-frame target resolution,
  and measured per-frame PMTiles size → the real 14-day footprint.
- **Per-frame PMTiles vs loose XYZ tiles** — PMTiles recommended (one file/frame,
  range-read, no inode blow-up), but confirm MapLibre raster-from-PMTiles source
  swapping animates smoothly on a phone; fall back to loose tiles + a
  `/tiles/radar/<frame>/{z}/{x}/{y}.png` route (mirrors the USGS route) if not.
- **National-only vs + van-centered regional capture** — start national-only
  (view zooms in for "nearby"); add a van-centered higher-res series only if
  state-level detail from the national mosaic disappoints.
- **Frame-index store** — filesystem glob of frame-keyed PMTiles is enough to
  start; add a `radar_frames` table only if capture metadata / paging is wanted.

---

## Integration points

- `api/tile_layers.py` — registry seam (paths/env for the radar archive dir).
- `api/routes/tiles.py` — `osm_pmtiles` is the copy-model for the per-frame route.
- `api/routes/` — new `radar.py` (or `weather.py`) blueprint for `/api/radar/*`.
- `web/src/lib/map.ts` — `pmtiles://` protocol already registered; raster
  add/remove + `setLayoutProperty` visibility is the animation machinery.
- `web/src/lib/routes.ts` / `Shell.svelte` — new `/weather` destination.
- `deploy/` — `radar-fetch.service` + `.timer`; deploy-hook restart branch.
- CLAUDE.md + a new `.claude/modules/weather.md` when it lands (fold this plan in).
