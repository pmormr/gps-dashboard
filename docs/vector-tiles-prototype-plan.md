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

## Phase 5 — Integration into gps-dashboard (ACTIVE)

Prototype complete, all gates passed — now executing the real integration. This
is a live checklist; check items off as they land.

### Scope change: North America, not just CONUS

The Pi's storage is ~1 TB, so the extent was widened from CONUS to **all of
North America** (the 33 GB archive is still trivially small). The size/overzoom
findings from the prototype carry over unchanged.

- [x] **Archive retrieval — DONE.** North America, full bbox
      `-168,7,-52,72` (Alaska east of the dateline, all of Canada, Mexico,
      Central America to Panama; skips Greenland ice + high arctic), z0–15.
      Built + `verify`-clean on the NAS at
      `/volume2/scratch/vector-tiles-lab/out/northamerica.pmtiles`: **33 GB**,
      **42,299,310 tile entries** (37.3M deduped contents), mvt+gzip, bounds
      exact, z0–15 — matches the dry-run estimate. Single extract, 10m52s at
      ~60 MB/s. Source build `https://build.protomaps.com/20260606.pmtiles`.

      Test server live on the NAS (`serve.py`, `0.0.0.0:8000`, no firewall block
      on the LAN): `http://10.1.100.224:8000/harness/seam.html` renders the full
      NA base + trip overlay clean from a LAN browser (headless-verified, 0
      errors). Phone render check against the real NA archive can be done here.

      **Run on the UGREEN NAS (`rex-nas.rex.pmormr.com`), not the laptop** — it
      has a 1 Gb internet link. Debian 12, x86_64 (same arch as the laptop, so
      the `pmtiles` binary is portable; here it was re-downloaded from the
      go-pmtiles v1.30.3 release). Scratch at
      `/volume2/scratch/vector-tiles-lab/` (6.4 TB free). A **single** extract
      with HTTP/1.1 forced (`GODEBUG=http2client=0`) and `--download-threads=8`
      runs at **~60 MB/s** (~9 min for the whole 33 GB), so banding is
      unnecessary on this link:
      ```
      GODEBUG=http2client=0 ./pmtiles extract \
        https://build.protomaps.com/20260606.pmtiles out/northamerica.pmtiles \
        --bbox="-168,7,-52,72" --minzoom=0 --maxzoom=15 --download-threads=8
      ```
      Background-detach it (`nohup … &`, log to a file) so an SSH drop can't kill
      it. **Fallback for slow/fragile links:** the banded extract + `pmtiles
      merge` (`extract-na.sh`) — the laptop's single 33 GB attempt died at 99% on
      a transient HTTP/2 stream error (no resume), which is what forcing HTTP/1.1
      and/or banding avoids.

      Testing (harness render, phone check) also moves to the NAS: it serves the
      archive + harness on the LAN; laptop/phone browsers point at the NAS IP.
- [ ] **Copy to Pi NVMe.** `rsync` from the NAS scratch to
      `/mnt/nvme/tiles/northamerica.pmtiles` on the Pi (atomic: `.tmp` then `mv`).
      Treat like the DB/cache: persists across deploys, not in git, not touched
      by the post-receive hook.
- [ ] **Flask serving.** Add a route serving the `.pmtiles` with HTTP range
      support (`send_file(..., conditional=True)`). This **replaces** the OSM
      tile proxy + per-tile PNG cache + `precache.py`-for-OSM. USGS raster keeps
      the existing proxy/cache; `precache.py` stays relevant only for USGS.
- [ ] **Vendoring** → `static/vendor/`: MapLibre GL JS, `maplibre-gl-leaflet`
      (`leaflet-maplibre-gl.js`), the style JSON, glyphs, sprite. Leaflet 1.9.4
      is already vendored.
- [ ] **Frontend (`map.js`), Decision #2 option A.** Add the vector base via
      `L.maplibreGL`; carry over the absolute-sprite-URL fix (fetch style as an
      object, rewrite `sprite`/`glyphs` against `location.origin`). Keep trip
      rendering, live mode, FAB, two-map setup untouched. Reconcile the layer
      dropdown (vector OSM base + raster USGS) and retire the now-meaningless
      "↻ refresh" checkbox for the vector layer.
- [ ] **Label/POI density control.** Port the `labels.html` panel (POI category
      toggles, density min-zoom-offset slider, minor-street-names toggle) — now
      validated **inside the Leaflet seam** (`harness/seam.html`) driving the
      inner MapLibre map via the plugin's `getMaplibreMap()`. Mechanics confirmed
      against the NA archive: `setFilter("pois", ["all", ["in",["get","kind"],…],
      [">=",["zoom"],["+",["get","min_zoom"], offset]]])` and
      `setLayerZoomRange("roads_labels_minor", on?13:24, 24)`. Re-apply on the GL
      map's `styledata` (survives base-layer swaps). Layer ids `pois` /
      `roads_labels_minor` exist in the shipped Protomaps "light" style.

      **Gotcha — surfaced kinds render invisible without a color fix.** The
      `pois` layer's *original* filter only allows the kinds the style is built
      for; van-critical kinds (`college`, `fuel`, `hospital`, `hotel`, `parking`,
      `bank`, …) are excluded. Overriding the filter to surface them is not
      enough: the layer's `text-color` is a `case` on `kind` with no branch for
      those kinds, so they fall to the fallback `#e2dfda` — identical to
      `text-halo-color` `#e2dfda`, i.e. the label is invisible (halo shows, glyphs
      don't). Fix in the harness: recolor only the fallback (last element of the
      `text-color` case) to a readable dark `#3a3a3a` via `setPaintProperty`,
      preserving the per-category colors. At integration, consider giving the
      surfaced kinds proper category colors (and icons — the light sprite lacks
      fuel/hospital/lodging/parking/bank icons, so those render text-only today).
- [ ] **Pi validation:** render performance on the real phone over van WiFi;
      confirm offline.
- [ ] **Docs:** update the Tile Proxy & Cache section of `CLAUDE.md` to describe
      the vector basemap + raster USGS split.

### Updating the archive

A `.pmtiles` archive is a single immutable, clustered, compressed file — the
format has **no in-place patch/merge/delta** mechanism. There is therefore no
per-tile conditional refresh; the raster layer's `?refresh=1` ETag-per-tile
trick has no vector equivalent (this is why the "↻ refresh" checkbox is retired
for the vector layer). Updating OSM data means a **full re-extract + full file
replacement**:

```
# 1. On the laptop, re-extract against a newer dated planet build:
pmtiles extract https://build.protomaps.com/<NEWER-DATE>.pmtiles \
  out/northamerica.pmtiles --bbox="-168,7,-52,72" --minzoom=0 --maxzoom=15

# 2. Replace on the Pi atomically (dashboard serves this file live):
rsync -P out/northamerica.pmtiles \
  pmorgan@192.168.42.178:/mnt/nvme/tiles/northamerica.pmtiles.tmp
ssh pmorgan@192.168.42.178 \
  "mv /mnt/nvme/tiles/northamerica.pmtiles.tmp /mnt/nvme/tiles/northamerica.pmtiles"
```

- `build.protomaps.com/<date>.pmtiles` is a dated daily planet build (pinned to
  `20260606` here); point at a newer date to pull fresher OSM data. The extract
  still only fetches the bbox via range requests (not the 136 GB planet), but the
  **copy to the Pi is a full ~33 GB transfer** — rsync's delta saves little since
  re-extraction recompresses/reorders tiles throughout.
- Regenerate with the **banded `extract-na.sh`** (HTTP/1.1 + per-band extract +
  `merge`), not a single 33 GB extract — the one-shot transfer is fragile at this
  size (a 99% HTTP/2 failure loses everything; there is no resume).
- **Replace atomically** (write `.tmp`, then `mv`/rename) so an in-flight range
  request can't read a torn file — same ethos as the existing atomic raster tile
  writes.
- **Cadence: rare and deliberate, not routine.** Basemap geometry changes slowly
  and the van is off-grid most of the time; treat this as occasional manual
  maintenance (≈yearly, when the laptop has internet), not a background refresh.
  The trade vs. raster: a stable, offline, one-file basemap instead of live
  freshness.

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

_Session 1 (2026-06-06), Colorado end-to-end on the Kubuntu laptop. Scratch:
`~/vector-tiles-lab/` (out of git)._

### Generation path changed: Protomaps extract, not planetiler

The plan assumed planetiler (generate tiles from a Geofabrik `.osm.pbf`, needs
Java, multi-hour CONUS gen). We instead used **Protomaps' pre-built planet** and
`pmtiles extract` to pull a regional clip via HTTP range requests. No Java, no
generation step, minutes instead of hours, and Protomaps ships a matching style
flavor + offline glyph/sprite assets. Trade-off: locked to the Protomaps tile
schema/style rather than full schema control (see the labels finding below —
the schema turned out to be plenty rich). Planetiler stays the fallback only if
we ever need an OSM tag/POI kind Protomaps' schema omits entirely.

Tooling: `pmtiles` CLI 1.30.3 (go-pmtiles); source build
`https://build.protomaps.com/20260606.pmtiles` (136 GB planet, schema v4.14.9);
`maplibre-gl` 5.24, `pmtiles` JS 4.4, `@protomaps/basemaps` 5.7 (style gen).

### Decisions settled

- **Decision #1 (serving format): PMTiles, single file, HTTP range.** Confirmed.
  Served the one `.pmtiles` over a small range-capable static server
  (`serve.py`); the browser pulls tiles via the pmtiles JS `addProtocol`. No
  proxy, no per-tile cache. On the Pi this maps to Flask `send_file(...,
  conditional=True)`.
- **Decision #3 (style): Protomaps "light" flavor.** Roads/towns/water/labels
  legible for van use. Generated via `@protomaps/basemaps` `layers()` +
  `namedFlavor("light")`; 71 layers, source-layers match the archive 9/9.
- **Decision #5 (zoom range): z0–15 baked.** The Protomaps planet bakes to z15;
  overzoom handles deeper. (Plan guessed z0–14; the source gives 15 for free.)
- **Decision #6 (glyphs): Protomaps `basemaps-assets` fonts, Noto Sans
  Regular/Medium/Italic only** (~14 MB, the three stacks the light style
  references) + the `light` sprite. Glyph/sprite URLs rewritten to local paths.
- **Decision #2 (frontend):** still leaning option A (keep Leaflet, add the
  vector basemap via `maplibre-gl-leaflet`) — **not yet exercised** (deferred to
  the integration session). The harness used a bare MapLibre map, not Leaflet.
- **Decision #4 (extent packaging): deferred** — CONUS not yet extracted (see
  below). Colorado is one 485 MB file; CONUS-as-one-file is the open question.

### Measured

- **Colorado, all zooms z0–15, one file: 485 MB.** Extract ~4 min via range
  requests against the 136 GB planet (no full download). 362,695 unique tiles,
  mvt+gzip, bounds exact. RAM negligible (extract is I/O, not compute — the
  planetiler RAM concern doesn't apply to the extract path).
- **CONUS: not measured this session** (gated before scale-up). Same cheap
  `pmtiles extract` with a lower-48 bbox; this is the headline size number for
  the go/no-go and Decision #4. Direction is clear — 485 MB for all of Colorado
  to z15 vs. the raster baseline (CO alone would be many GB past z12).
- **Overzoom quality:** crisp z15→~z18 (client scales vector geometry). Pass.
- **Offline test:** harness loads with **zero** external network requests —
  tiles, style, glyphs, sprite all local; no external URLs remain in style or
  page. Pass.
- **Smoothness:** smooth pan/zoom on the laptop. **Phone test deferred** (the
  real mobile gate — next session, served over the LAN).

### Bonus finding — label density/categories are a frontend-only lever

Inspecting the tiles directly (decoded Denver tiles): the data is far richer
than the "light" style draws. A z12 Denver tile carries ~196 named roads, ~41
named POIs (hospitals, hotels, retail, museums…), ~46 named places. The default
style mutes them — its POI label layer is gated by `zoom >= min_zoom + 0`, and
van-critical kinds (`fuel`, `hospital`, `parking`, `hotel`) are excluded by the
filter outright. All recoverable client-side via `setFilter` / zoom-range tweaks
— **no re-extraction, no external data, no Google** (whose terms forbid offline
caching anyway; OSM is the ceiling and it's already in the file). Prototyped a
live control panel (`harness/labels.html`): POI category toggles + a label
density slider + minor-street-names toggle. This becomes a small `map.js`
control at integration, not a precache decision.

### Notes for Phase 5 (integration)

- **Attribution:** set the source `attribution` to `© OpenStreetMap, Protomaps`
  (data is Protomaps-built from OSM; OSM credit is ODbL-required). MapLibre adds
  its own credit via `AttributionControl` — keep it. No proxy is involved, so no
  proxy attribution.
- **Label toggles** as a frontend control (`setFilter` on the `pois` layer +
  `setLayerZoomRange` on road-label layers).
- POI icons were tried and rejected: color emoji can't render in MapLibre SDF
  text, and inline monochrome BMP symbols looked poor. If per-category icons are
  wanted later, use sprite `icon-image` (the light sprite lacks fuel/hospital/
  lodging/parking/bank, so those few would need adding).

### Recommendation

**Proceed with the vector migration via the Protomaps-extract path** — pending
the two deferred gates: **CONUS extract size** and **on-phone render
performance**. Everything testable on the laptop passed (size direction,
offline, overzoom, smoothness), and the path is markedly simpler than planetiler
(no Java, no generation, official offline assets). Next session: extract CONUS
for the headline number, then exercise Decision #2 option A (maplibre-gl-leaflet)
and the phone test before committing to integration.

---

## Results — Session 2 (2026-06-06)

Closed the CONUS size gate and Decision #2; one gate (on-phone render) remains.

### CONUS size gate — PASS

`pmtiles extract` with `--dry-run` (no download) against the planet, bbox
`-125.00,24.50,-66.50,49.50` (matches `precache.py`'s `conus`), z0–15:

- **~18 GB archive** (19 GB transferred at 5% overfetch), **13.8M** result tile
  entries from 20.6M region tiles, computed in **23s**. RAM negligible (I/O).
- Well under the < 30 GB target. Against the raster baseline (74 GB for OSM z14
  *alone*; 295 GB z5–15) this is ~16× smaller with free crisp overzoom past z15.
- **Decision #4 settled: one single-file `.pmtiles`** (no per-state split).
- The actual download was **deferred to integration** — the dry-run answers the
  size gate and Decision #4, and render perf is provable on the Colorado file.
- **Scope later widened to all of North America (~33 GB)** given the Pi's ~1 TB;
  this CONUS number stands as the original gate measurement. See Phase 5.

### Decision #2 (frontend) — settled: option A

Built `harness/seam.html`: the Protomaps vector base loaded **inside a Leaflet
1.9.4 map** via `@maplibre/maplibre-gl-leaflet` (`L.maplibreGL`), with a fake
trip polyline + start/end `circleMarker`s as ordinary Leaflet overlays, and an
`L.control.layers` base switch between the vector GL base and a raster layer
(online OSM raster standing in for USGS). Headless-rendered clean (0 console
errors); overlays coexist and pan/zoom in sync with the GL base. **Option A
confirmed** — the existing Leaflet overlay/trip/marker/FAB code can stay; only
the base layer changes.

### Gotcha for Phase 5 — absolute sprite URL required

MapLibre GL **rejects a relative or root-relative `sprite` URL** ("must be
absolute"); glyphs are more lenient but were normalized too. Fix used in both
harness pages: fetch the style JSON as an object and rewrite `sprite`/`glyphs`
against `location.origin` at runtime, then pass the object to MapLibre. This is
portable across localhost and the LAN IP and should carry into the real app's
`map.js` (or be baked absolute when Flask serves the style). The pmtiles
*source* URL tolerates root-relative (`pmtiles:///out/...`).

### Vendored for integration

`@maplibre/maplibre-gl-leaflet` → `leaflet-maplibre-gl.js` (13 KB). Leaflet 1.9.4
is already vendored in the project. MapLibre GL JS + pmtiles JS + style/glyphs/
sprite still to be vendored at integration.

### On-phone render performance — PASS

Served the harness over the LAN (`0.0.0.0:8000`) and opened `seam.html` on the
actual phone client. Pan/zoom smooth after load; the initial load was mildly
flaky over WiFi but fine on refresh (cold range-request burst, not a render
issue). Required opening the laptop firewall: ufw was active with default-deny
incoming, so a scoped rule was needed —
`sudo ufw allow from <lan>/24 to any port 8000 proto tcp` (dev-laptop only; not
relevant to the Pi, which serves on its own LAN).

Harness fix for the phone: in portrait the HUD overlapped Leaflet's zoom control
and the layers control overran the width — moved the HUD to bottom-left with a
max-width and collapsed the layers control to a tap icon.

### All gates passed — proceed to integration (Phase 5)

Size ✅, offline ✅, overzoom ✅, on-phone smoothness ✅, and a Leaflet-preserving
integration path (Decision #2 option A) ✅. No remaining prototype gates. The
only deferred *task* is the actual `pmtiles extract` download — now widened to
North America (~33 GB) and tracked as the first item of Phase 5.
