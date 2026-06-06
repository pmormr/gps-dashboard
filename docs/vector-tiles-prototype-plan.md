# Vector Tiles Prototype Plan

## Context

The app currently uses **raster** map tiles: pre-rendered PNG/JPEG images cached
one-per-`z/x/y` via `tools/precache.py` and the Flask tile proxy
(`api/routes/tiles.py`, layers in `api/tile_layers.py`). Raster storage grows
~4× per zoom level because every level is a fresh full-resolution render, so
nationwide offline coverage is impractical past a shallow zoom:

| Scope (CONUS, lower-48) | Raster tiles | OSM @~15KB | USGS @~35KB |
|---|---:|---:|---:|
| cumulative z5–12 | 325K | ~4.6 GB | ~11 GB |
| cumulative z5–13 | 1.3M | ~18.5 GB | ~43 GB |
| cumulative z5–14 | 5.2M | ~74 GB | ~172 GB |
| cumulative z5–15 | 20.6M | ~295 GB | ~688 GB |
| z16 alone | 61.8M | ~1.18 TB | ~2.75 TB |

(Counts computed with `precache.py`'s own tile math; sizes are estimates.)

**Vector tiles** store the underlying geometry + attributes (roads as polylines,
water as polygons, labels as points) in a compact protobuf format (MVT), gzipped,
and the *client* (MapLibre GL, WebGL) rasterizes to pixels on the device. Two
consequences motivate this prototype:

1. **Compression:** the entire US, all zooms, is expected to be ~10–30 GB as one
   vector archive — vs. 74 GB for raster z14 *alone*. (Estimate to be measured.)
2. **Free crisp overzoom:** vector cached to z14 renders sharply at z15–z20+
   because the client just scales geometry. The 4×-per-zoom storage tax above z14
   disappears.

This collapses the original "how detailed a zoom is practical" question.

### Goal of this prototype

Decide, with **measured local evidence**, whether to migrate the OSM basemap from
raster to vector — and if so, lock in the serving format, the style/asset
pipeline, and the frontend integration approach — **before** touching the
production app. All prototype work happens locally on the Kubuntu laptop (faster
CPU + internet). Integration into `gps-dashboard` and Pi testing are deferred to
a follow-up effort (Phase 5 is a design output, not executed here).

---

## Constraints carried from the project

- **Offline-first.** Final design must render with **zero network**: vector
  archive, MapLibre GL JS, style JSON, font glyphs, and sprite all served
  locally. Airplane-mode test is a hard gate.
- **Mobile-first.** Primary client is a phone browser over van WiFi. WebGL
  render performance on the actual phone is a success criterion, not an
  afterthought.
- **No CDN at runtime.** Anything the final app needs gets vendored into
  `static/vendor/` (during integration, not prototype).
- **USGS Topo stays raster.** Vector replaces only the OSM basemap. USGS Topo is
  server-rendered imagery with no easy self-hosted vector equivalent; the
  existing raster proxy/cache stays for that layer. The "↻ refresh" checkbox
  loses meaning for vector and would apply only to the remaining raster layer.

---

## Decisions to make during the prototype

These are the questions the prototype exists to answer. Record the choice +
rationale in the Results section as each is settled.

| # | Decision | Options | Leaning |
|---|----------|---------|---------|
| 1 | Serving format | **PMTiles** (single file, HTTP range, no server process) vs MBTiles (SQLite + extractor route) | PMTiles — fits the "simple, local, one file" ethos; replaces the whole proxy+cache |
| 2 | Frontend integration | **A:** keep Leaflet, add vector basemap via `@maplibre/maplibre-gl-leaflet` (`L.maplibreGL`), keep all polyline/marker/FAB/control code; **B:** full MapLibre GL rewrite of `map.js` | A — contains blast radius to the base-layer setup |
| 3 | Basemap style | OSM Bright / Positron / MapTiler Basic (open) / custom | TBD by visual check for van use (roads + towns legible) |
| 4 | US extent packaging | one CONUS `.pmtiles` vs per-state files | one file if size allows |
| 5 | Baked zoom range | z0–14 typical (overzoom handles deeper) | z0–14 |
| 6 | Glyph fonts | Noto Sans (Latin ranges only, ~few MB) vs full unicode | Latin ranges |

---

## Environment / scratch layout

Keep all prototype artifacts **out of the git repo** — the `.pbf`, `.pmtiles`,
and tool jars are large binaries and must not be committed. Only this plan and a
results writeup live in the repo.

```
~/vector-tiles-lab/            # scratch, NOT in repo
├── planetiler.jar
├── sources/                   # downloaded .osm.pbf extracts
├── out/                       # generated .pmtiles / .mbtiles
├── style/                     # style.json + glyphs/ + sprite/ (offline-rewritten)
├── harness/                   # minimal MapLibre test page + vendored libs
└── results.md                 # measured numbers (copy findings back into repo plan)
```

Verify tool versions against current upstream docs before relying on flags below
— planetiler/MapLibre/pmtiles CLIs evolve and the exact flags here may drift.

---

## Phase 0 — Prerequisites (laptop)

- [ ] Confirm Java for planetiler: `java -version` (needs a current LTS JRE, 21+).
      Install via `apt` if missing.
- [ ] Confirm free disk: budget ~50–100 GB for pbf + planetiler temp + output.
- [ ] Download `planetiler.jar` (latest release) into `~/vector-tiles-lab/`.
- [ ] Download `pmtiles` CLI (go-pmtiles) for inspection/serving, or note the
      plan to serve via a range-capable static server.
- [ ] Pick a **small** first region to validate the pipeline fast: `colorado`
      (already a region in `precache.py`). Scale to North America / CONUS only
      after the small run renders correctly.

---

## Phase 1 — Generate vector tiles

Start small (Colorado) to shake out the pipeline, then scale up.

- [ ] Generate from a Geofabrik extract with planetiler (default profile is
      OpenMapTiles schema). Representative invocation — confirm flags via
      `java -jar planetiler.jar --help`:
      ```
      java -Xmx8g -jar planetiler.jar --download --area=colorado \
           --output=out/colorado.pmtiles
      ```
      `--area` auto-downloads the Geofabrik extract; output extension selects
      format (`.pmtiles` or `.mbtiles`).
- [ ] Record: **output size**, **generation wall-clock**, **peak RAM**.
- [ ] Scale up: `--area=us` (or `north-america`). Re-record the same metrics.
      This is the headline number for Decision #4 (one CONUS file vs per-state).
- [ ] Sanity-check the archive with `pmtiles show out/<file>.pmtiles` (zoom
      range, tile count, bounds, compression).

---

## Phase 2 — Serve + render locally, offline-faithful

The point is to mirror the eventual self-hosted/offline setup, not to use a
hosted demo.

- [ ] **Style + assets, made fully local.** Obtain a free OpenMapTiles-compatible
      style (OSM Bright / Positron / MapTiler Basic open). Download its **font
      glyph PBFs** (Latin ranges) and **sprite** (png + json), then rewrite the
      style JSON's `glyphs`, `sprite`, and `sources` URLs to local paths. This
      is the offline-completeness gotcha — labels silently fail without glyphs.
- [ ] **PMTiles serving (Decision #1, option A).** Serve the `.pmtiles` over a
      range-capable static server and load it in-browser via the pmtiles JS
      protocol. Confirm tiles resolve from the single file via range requests.
- [ ] **Minimal harness.** One HTML page: vendored MapLibre GL JS + local style +
      local pmtiles. Verify pan/zoom, and explicitly test **overzoom past z14**
      (zoom to z18 and confirm geometry stays crisp).
- [ ] **(Optional) MBTiles comparison** if PMTiles serving proves awkward: serve
      the same data as `.mbtiles` via a tiny SQLite-blob route to compare
      ergonomics. Decide #1.

---

## Phase 3 — Evaluate against success criteria

Record every measurement in `results.md`, then copy the summary into this plan's
Results section.

- [ ] **Storage:** US all-zooms vector size vs the raster baseline table above.
      Target: CONUS vector < ~30 GB.
- [ ] **Overzoom quality:** visual z14→z18 check — crisp, not blurry.
- [ ] **Phone performance:** serve the harness on the LAN, open on the *actual
      phone client*, assess pan/zoom smoothness and memory. This is a gate — the
      whole point is the phone.
- [ ] **Offline completeness:** airplane-mode / devtools-offline test — confirm
      **zero** network requests (tiles, style, glyphs, sprite all local).
- [ ] **Style suitability:** roads, towns, and labels legible and useful for van
      navigation at the zooms actually used. Settle Decision #3.

---

## Phase 4 — Frontend integration approach (prototype the seam)

Validate the low-risk integration path before committing the real app to it.

- [ ] In the harness, add the basemap via `@maplibre/maplibre-gl-leaflet`
      (`L.maplibreGL({ style })`) inside a **Leaflet** map — i.e. exercise
      Decision #2 option A, not a bare MapLibre map.
- [ ] Draw a fake trip **polyline** + markers as Leaflet overlays on top of the
      GL basemap; confirm they coexist and pan/zoom in sync.
- [ ] Add a second, raster Leaflet layer (any XYZ source standing in for USGS)
      and confirm a layer switch between the GL vector base and the raster layer
      works — this mirrors the production osm/usgs dropdown.
- [ ] Settle Decision #2 (A vs B) with rationale.

---

## Phase 5 — Integration design output (NOT executed in prototype)

Once Phases 1–4 lock the decisions, produce the concrete change list for the real
`gps-dashboard` integration. This phase yields a written plan, not code.

- **Serving:** add PMTiles serving to Flask with HTTP range support (or the
  chosen alternative). The proxy + per-tile PNG cache + `precache.py` machinery
  is **replaced for OSM** by shipping one `.pmtiles` file; USGS raster keeps the
  existing proxy/cache. `precache.py` stays relevant only for the USGS layer.
- **Vendoring:** MapLibre GL JS, `maplibre-gl-leaflet`, style JSON, glyphs,
  sprite → `static/vendor/`.
- **Frontend:** `map.js` base-layer changes per Decision #2; keep trip
  rendering, live mode, FAB, two-map setup. Reconcile the layer dropdown
  (vector base + raster USGS) and the now-vector-irrelevant "↻ refresh" checkbox.
- **Archive placement on Pi:** the `.pmtiles` is large and not in git — generate
  off-Pi, copy to NVMe (e.g. `/mnt/nvme/tiles/us.pmtiles`), and treat it like
  the DB/cache (persists across deploys, not overwritten by the post-receive
  hook). Document the copy step (scp/rsync).
- **Pi validation:** render performance on the real phone over van WiFi; confirm
  offline.
- **Docs:** update the Tile Proxy & Cache section of `CLAUDE.md` to describe the
  vector basemap + raster USGS split.

---

## Success criteria (go/no-go)

Migrate only if **all** hold:

1. CONUS vector archive is materially smaller than raster (target < ~30 GB, all
   zooms) **and** renders offline with no network.
2. Overzoom is crisp to ~z18.
3. Pan/zoom is smooth on the actual phone client.
4. A frontend integration approach is chosen that keeps the existing Leaflet
   overlays (trips, markers, live mode) working — ideally Decision #2 option A.

If any fail, document why and stay on throttled raster precaching.

---

## Results

_(Fill in as the prototype runs — measured numbers, decisions settled with
rationale, and the final go/no-go recommendation. This section is the durable
output that feeds the integration effort.)_

- Decision #1 (serving format):
- Decision #2 (frontend):
- Decision #3 (style):
- Decision #4 (extent packaging):
- Decision #5 (zoom range):
- Decision #6 (glyphs):
- Measured: Colorado archive size / gen time / RAM:
- Measured: CONUS (or NA) archive size / gen time / RAM:
- Overzoom quality:
- Phone performance:
- Offline test:
- **Recommendation:**
