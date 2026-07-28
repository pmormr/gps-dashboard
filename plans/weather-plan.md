# Weather subsystem plan (radar first)

**Status:** shaped 2026-07-28; refined + verified against the live service
2026-07-28 (session two), not started. Radar is layer one; the subsystem is
designed to grow other NWS/NOAA weather layers behind one registry — the
package, units, and routes are named **`weather`** from day one so layer two
is a registry entry, not a rename.

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

**Hard constraint from the source (verified live):** the service documents a
**4-hour moving window**; the observed window has run as low as ~2 h. So:
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

## Data source (verified live 2026-07-28 — no key, public domain)

NWS **`radar_base_reflectivity_time`** ImageServer (MRMS quality-controlled
base reflectivity), the documented machine backend behind radar.weather.gov
(the website itself moved to a tile renderer; we target the GIS service, never
scrape the site).

- Base URL: `https://mapservices.weather.noaa.gov/eventdriven/rest/services/radar/radar_base_reflectivity_time/ImageServer`
- **Web Mercator native** (WKID 102100/3857) — matches our tile grid, no
  reprojection. Native pixel size **564.77 m ≈ z8** (z8 = 611 m/px, so z8 is
  the natural max slice zoom; z9 would upsample).
- Export caps: **`maxImageWidth=15000`, `maxImageHeight=4100`** (not ~4096²).
  CONUS at native res ≈ **11,700 × 6,150 px**, so one frame = **2 exports**
  (vertical split, ~11,700 × ~3,075 each + overlap margin) — the "bbox grid"
  collapses to a pair.
- **Fetch = REST `exportImage`** (verified working): `bbox`, `bboxSR=3857`,
  `imageSR=3857`, `size=W,H`, `format=png32`, `transparent=true`,
  `time=<epoch-ms>`, `f=image` → transparent RGBA PNG. Epoch-ms `time` matches
  the frame key we store — one API surface for enumerate + fetch.
- **Frame enumeration = the REST catalog query**, *not* `timeExtent` and *not*
  WMS: `.../ImageServer/query?where=1=1&outFields=idp_validtime,idp_validendtime`
  `&geometry=<CONUS envelope>&geometryType=esriGeometryEnvelope`
  `&spatialRel=esriSpatialRelIntersects&returnGeometry=false&f=json`.
  The service-level `timeExtent` is just `[start, end]`, and the WMS time
  dimension advertises a *continuous* interval (`PT1S`) — neither lists frames.
- **Two rasters per update cycle:** even CONUS-filtered, the catalog returns
  *pairs* of rasters ~40–60 s apart every ~6–8 min (what the pair is — split
  coverage vs. overlapping products — is a P0 question). Bucket catalog
  timestamps within ~2 min into one **frame instant**; export with that
  instant's `time` and let the mosaic rule composite whatever is valid then.
- Observed cadence: **~6–8 min** between cycles (service says "every 5
  minutes"). 14-day archive ≈ **~2,900 frames**.
- Coverage: the mosaic dataset spans CONUS + AK/HI/Caribbean/Guam; our CONUS
  envelope (≈ lon −126.3..−66.9, lat 24.9..49.4; exact bbox nailed in P0)
  excludes AK/HI — fine for a lower-48 van; note it.
- WMS 1.3.0 fallback exists but lives at the **non-`/rest/`** URL
  (`https://mapservices.weather.noaa.gov/eventdriven/services/radar/radar_base_reflectivity_time/ImageServer/WMSServer`),
  wants ISO8601 `TIME`, and still needs the REST catalog for enumeration —
  kept as fallback only.
- Requests carry the repo's identifying User-Agent convention
  (`gps-dashboard/1.0 (email)`, the `api/routes/tiles.py` pattern).

GeoServer alternative (kept as a note, not the primary): `conus_bref_qcd` WMS at
`https://opengeo.ncep.noaa.gov/geoserver/conus/conus_bref_qcd/ows`.

**New dependency (user-approved 2026-07-28):** `pmtiles` (PyPI, 3.7.0,
pure-Python, zero deps) — reader *and writer*. Runtime dep (the Pi fetcher
writes archives); the Flask serving path just streams bytes and doesn't need it. Pillow (mosaic/slice) and httpx
are already project deps.

---

## Architecture

Three parts, each mapped to an existing pattern in the repo.

### 1. Fetcher — timer-driven oneshot (the `gps-db-backup` / `gps-drone-sync` shape)

- New **`weather/`** package (pure helpers: frame bucketing, export-grid math,
  mosaic, tile slicing, retention selection — clockless + table-testable,
  mirroring `radio/`), plus a thin `tools/fetch_weather.py` CLI, driven by
  `deploy/weather-fetch.timer` + `weather-fetch.service` (`Type=oneshot`,
  `After=network-online.target`). The **layer registry lives in `weather/`
  from day one**; radar is entry one.
- **Timer every ~5 min**; capture at the native **~6–8 min cadence** (dedup by
  frame key, so a tick that finds no new upstream frame is a no-op).
- Per run:
  1. Reachability check (GET `?f=json`, short timeout). Offline → log + exit 0
     (clean no-op, like `ssh_reachable` in `backup_db`).
  2. Catalog query (CONUS envelope) → bucket raster timestamps (~2 min) →
     frame instants available upstream.
  3. Diff against what's on disk; for each missing frame run the **2-export
     fetch** at native res, mosaic to one Web-Mercator master, slice to a
     sparse tile pyramid, pack to a per-frame PMTiles — written tmp-then-rename
     (the `_atomic_write` convention in `api/routes/tiles.py`), so readers
     never see a partial archive.
  4. Prune frames older than **14 days** (prune before fetch, so a full disk
     can't wedge capture).
- Must handle `KeyboardInterrupt` → `"\nInterrupted."`, exit 130 (tools rule).

### 2. Storage + delivery — tile the radar, don't ship a big image

The delivery cost is the governing constraint, not storage (NVMe has room; <50 GB
is negligible). A single CONUS PNG per frame means the browser downloads the
whole national image even when zoomed into one state, and decodes a multi-MB
image per animation step on a phone. So we **tile the radar like a slippy layer**
(the `{z}/{x}/{y}` model of the USGS/OSM layers): the browser pulls only the
tiles in the current viewport at the current zoom, and high capture resolution
stops fighting cheap delivery.

- **Per-frame PMTiles (recommended format):** slice each master into an image
  pyramid (transparent PNG tiles, sparse — skip empty tiles) packed into **one
  PMTiles file per frame**, named by the frame instant (epoch-ms int). Reuses
  the exact `osm.pmtiles` serving path (`send_file(..., conditional=True)`
  byte-range) — the browser range-reads only the tiles it needs, one file per
  frame avoids inode blow-up, and MapLibre reads it via the already-registered
  `pmtiles://` protocol.
  - Zoom range **z2–z8** (native ≈ z8); overzoom beyond (raster sources scale
    past `maxzoom` for free).
- Routes: `GET /tiles/weather/radar/<frame_ts>.pmtiles` (range-served, mirrors
  `osm_pmtiles`; `<frame_ts>` validated digits-only — no traversal). Plus
  `GET /api/weather/radar/frames?window=<hours>` → JSON list of available frame
  timestamps (+ coverage/age) the frontend builds the timeline and pmtiles URLs
  from.
- **Archive health surfaces in the `/data` drill-in, not a bespoke endpoint:**
  a `readonly` `Chunk` entry in `updater/chunks.py` (owned by its timer, like
  the backup row) with a filesystem probe — newest-frame age + archive span +
  gap count. No `/api/weather/status`.
- Estimated footprint: ~2,900 frames × a few MB sparse ≈ **order 5–15 GB**.
  Measure in P0/P1 and tune; well under 50 GB.

### 3. View — new Weather tab (shared map engine)

- A new nav destination `/weather` **over the shared keep-alive MapLibre
  instance** (the Drive precedent — Weather is chrome + an overlay controller
  around the one engine, not a second GL context): a `weather.ts` overlay
  controller like `drone.ts`/`phone.ts`, **torn down on route leave** and
  re-installed on basemap style swaps by the existing idempotent handler.
- **Animation = the standard MapLibre radar-loop technique:** preload the current
  playback *window* (e.g. last 1–3 h = ~10–25 frames) as raster sources from
  their per-frame PMTiles and toggle layer visibility (`setLayoutProperty(..,
  'visibility', ..)`) frame to frame. **Do not** load all ~2,900 frames — the
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
3. **Best resolution:** yes — native-res export pair per frame beats the single
   export cap (2 exports, mosaic).
4. **Delivery:** tiled (per-frame PMTiles), so the browser only pulls the visible
   viewport. This is what makes "best resolution" and "cheap to render" compatible.
5. **Cadence:** capture every upstream cycle (~6–8 min); timer fires ~5 min.
6. **Source:** NWS `radar_base_reflectivity_time` via **REST `exportImage`**
   (verified; WMS is the fallback). Enumeration via the REST catalog query.
7. **Naming:** `weather` everywhere from day one (package, units, routes);
   radar is registry entry one.
8. **Map engine:** reuse the shared MapView + keep-alive host; Weather is an
   overlay controller, not a second Map.
9. **Status:** `/data` chunk-registry entry (readonly + fs probe), no bespoke
   status endpoint.

---

## Extensibility: the weather-layer registry (radar is layer one)

The fetcher + routes are built around a **registry in `weather/`** (one entry
per layer: id, source URL/service, bbox strategy, cadence, raster-vs-vector,
observed-vs-forecast, retention), so a new weather layer is a registry entry,
not new plumbing. Two pipelines:

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

- **P0 — Source spike (shrunk by the live verification).** Already confirmed:
  service params, export caps, native res, transparent png32 `exportImage`
  with `time`, catalog enumeration, the paired-raster cadence. Remaining:
  what the ~40 s raster pairs *are* (and that a bucketed `time` instant
  composites them correctly), mosaic seam check across the 2-export split
  (no visible edge), palette behavior when downscaling to z2–z7, the exact
  CONUS bbox, and a measured per-frame PMTiles size → the real 14-day
  footprint. *(One throwaway script.)*
- **P1 — Fetcher + storage.** `weather/` package (registry + pure helpers:
  bucketing/grid/mosaic/tile/retention) + `tools/fetch_weather.py` +
  `weather-fetch` timer/service units. Per-frame PMTiles on the NVMe, 14-day
  prune, atomic writes. Tests on the pure helpers. Add the `pmtiles` dep.
- **P2 — API + delivery.** `/tiles/weather/radar/<frame_ts>.pmtiles` (range) +
  `/api/weather/radar/frames`; the `updater/chunks.py` readonly entry + fs
  probe. Flask-client tests.
- **P3 — Weather view.** `/weather` tab over the shared engine (`weather.ts`
  overlay controller), windowed frame animation (visibility toggle),
  scrubber/play/opacity/center-on-van. Verify PMTiles-source animation is
  smooth on a phone — fall back to loose XYZ tiles +
  a `/tiles/weather/radar/<frame>/{z}/{x}/{y}.png` route if not.
- **P4 — Warnings layer** (vector) — the first proof the registry generalizes.
- **P5+ — GOES / SPC / NDFD** as separate phases.

Build + commit `static/dist/` before pushing (Pi never builds). Deploy hook:
`weather-fetch` is a timer-driven oneshot — idempotent timer re-enable on push
(the `gps-db-backup` pattern), no restart branch; the timer-enable block still
needs adding on the Pi. Enabled-gated until wired, like the sensor readers.

---

## Open questions to lock during build

- **P0 output:** exact CONUS bbox + overlap margin, per-frame PMTiles size →
  real footprint; what the paired rasters are.
- **Per-frame PMTiles vs loose XYZ tiles** — PMTiles first; the phone
  animation-smoothness check in P3 is the gate, loose tiles the fallback.
- **National-only vs + van-centered regional capture** — start national-only
  (view zooms in for "nearby"); add a van-centered higher-res series only if
  state-level detail from the national mosaic disappoints.
- **Frame-index store** — filesystem glob of frame-keyed PMTiles is enough to
  start (and is what the chunks probe reads); add a `weather_frames` table only
  if capture metadata / paging is wanted.

---

## Integration points

- `api/tile_layers.py` — registry seam (paths/env for the weather archive dir).
- `api/routes/tiles.py` — `osm_pmtiles` is the copy-model for the per-frame
  route; `_atomic_write` is the write convention.
- `api/routes/` — new `weather.py` blueprint for `/api/weather/*`.
- `updater/chunks.py` — the readonly radar-archive chunk + probe.
- `web/src/lib/map.ts` — `pmtiles://` protocol already registered; raster
  add/remove + `setLayoutProperty` visibility is the animation machinery.
- `web/src/lib/weather.ts` — the overlay controller (the `drone.ts` shape).
- `web/src/lib/routes.ts` / `Shell.svelte` — new `/weather` destination.
- `deploy/` — `weather-fetch.service` + `.timer`; Pi-side timer-enable block.
- CLAUDE.md + a new `.claude/modules/weather.md` when it lands (fold this plan in).
