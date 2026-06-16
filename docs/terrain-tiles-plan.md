# Terrain Tiles Plan

## Context

The vector OSM basemap (`northamerica.pmtiles`, ~33 GB) gives a flat 2D map.
This effort adds a **3D terrain mesh** under it, so a tilted view shows actual
ridges and valleys — primary use case is reviewing mountain drives with the GPS
track draped onto the terrain rather than floating at sea level.

Source: **Mapzen Terrarium** tiles, hosted as AWS Open Data at
`s3://elevation-tiles-prod/terrarium/` (anonymous reads, no creds). PNG tiles
where the pixels encode elevation in meters as RGB:

```
elevation_m = (R * 256 + G + B / 256) - 32768
```

MapLibre reads this format natively (`raster-dem` source, `encoding: 'terrarium'`)
and turns it into a terrain mesh client-side.

Why Mapzen specifically (decided in planning discussion): CONUS layer is built
from USGS NED 10m (same upstream as the existing USGS Topo raster), global
coverage outside CONUS, pre-tiled in Web Mercator, MapLibre-native encoding,
redistributable, single-archive friendly. The alternative — sourcing USGS 3DEP
directly and running our own tile pipeline (`rio-rgbify` + `gdal2tiles`) —
buys nothing at the zoom levels we care about (z12–z13 is already at NED's
native ~10 m sample density) and is days of work vs hours. The 3DEP path stays
open later if we want to upgrade specific regions to 1/9 arc-second (~3 m) or
LIDAR 1 m — same MapLibre integration, different archive.

This plan is **data-only**: build the archive, serve it from Flask, smoke-test
it loads in MapLibre. Wiring it into the actual `/` view (drop Leaflet, switch
to pure MapLibre, drape the GPS track, add pitch controls) is a separate
effort.

## Constraints carried from the project

- **Offline-first.** Archive must serve fully offline once built. No runtime
  S3, no runtime CDN. Same operational model as `northamerica.pmtiles`.
- **Mobile-first.** Phone is the primary client; terrain rendering must stay
  smooth on the phone GPU (tested in the follow-on integration effort, not
  here).
- **Build off-Pi.** The Pi (CM5) is too slow for the download + pack at this
  scale. Build on the dev laptop or NAS, ship the final `.pmtiles` to the Pi
  via `rsync` (same pattern as `northamerica.pmtiles`).
- **Atomic replace.** Pi-side install writes `.tmp`, then `mv` — never
  overwrite in place while Flask is serving the live archive.

## Decisions already made

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Coverage | **Full North America**, bbox `-168,7,-52,72` | Matches the existing `northamerica.pmtiles` bbox exactly — same operational coverage for both archives, no "terrain ends here, OSM keeps going" boundary. |
| 2 | Zoom range | **z0–z13** target, z12 fallback if Phase 1 sampling blows the disk budget | z13 looks smoother on tilt; native source (NED 10m) is at ~z12 resolution, so z13 is partly interpolation but still visibly better at high pitch. |
| 3 | Disk budget | ~200 GB on dev machine for the build, archive must fit comfortably alongside `northamerica.pmtiles` on Pi NVMe | User-provided constraint. |
| 4 | Source format | Mapzen `terrarium/` PNG | Only one MapLibre reads natively with no encoding step. |
| 5 | Distribution format | Single `.pmtiles` archive | Same model as OSM basemap; HTTP-range over one file; no per-tile cache. |
| 6 | Build location | Dev laptop | Same as OSM PMTiles build. NAS is acceptable fallback if the laptop's bandwidth is the bottleneck. |
| 7 | Integration scope | Data + serving only this round | The MapLibre/terrain integration is non-trivial (Leaflet drop) and gets its own plan. |

## Architecture

### Pipeline

```
AWS S3 (anonymous)
  └─► tools/fetch_terrain_tiles.py
        ├─► writes MBTiles directly (resumable, single SQLite file)
        └─► async HTTPS GET, bbox-filtered, bounded concurrency
              │
              ▼
       go-pmtiles convert
              │
              ▼
       northamerica-terrain.pmtiles  (dev laptop)
              │
              ▼ rsync (atomic .tmp → mv)
              │
       /mnt/nvme/tiles/northamerica-terrain.pmtiles  (Pi)
              │
              ▼
       Flask: GET /tiles/terrain.pmtiles (send_file, range-supported)
```

### Why MBTiles as the intermediate

The downloader could write a flat XYZ tree on disk, but tens of millions of
tiny PNG files thrash the filesystem and a single MBTiles SQLite is faster to
write, faster to convert, and trivially resumable (just check whether a row
exists before fetching).

### File paths and env vars

- **Dev machine, scratch:** `~/terrain-tiles-lab/` (out of git — large
  binaries must not be committed). Holds the MBTiles intermediate and the
  final `.pmtiles`.
- **Pi runtime:** `/mnt/nvme/tiles/northamerica-terrain.pmtiles` — sits next
  to `/mnt/nvme/tiles/northamerica.pmtiles`. Persists across deploys (same
  exclusion as the OSM archive and the SQLite DB).
- **Flask env var:** `GPS_TERRAIN_PMTILES_PATH` (parallel to the existing
  `GPS_PMTILES_PATH`). Dev fallback: `~/.cache/gps-dashboard/northamerica-terrain.pmtiles`.

## Phase 0 — Prerequisites (dev laptop)

- [ ] Confirm free disk: ~200 GB for MBTiles intermediate + final `.pmtiles`
      + scratch headroom. The MBTiles is the peak (it holds every PNG); the
      final `.pmtiles` is similar size; both exist simultaneously during
      conversion.
- [ ] Install `go-pmtiles`:
      - macOS: `brew install go-pmtiles`, OR
      - Download from https://github.com/protomaps/go-pmtiles/releases (latest
        release, your arch). Verify with `pmtiles version`.
- [ ] Python deps: `httpx` (async HTTP client). Add to `pyproject.toml` if not
      already present.
- [ ] Sanity-check bucket access: anonymous GET on a known tile, e.g.
      `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/0/0/0.png`
      should return a ~few-KB PNG.

## Phase 1 — Size sampling (decides z13 vs z12)

The mean Mapzen Terrarium tile size varies (~1–2 KB ocean, ~10–25 KB land,
~20–40 KB mountain). Estimating NA z0–z13 from first principles is rough; a
sample is more reliable than guessing.

- [ ] Pick a representative slice: **Colorado** bbox
      `-109.05,36.99,-102.04,41.00`, z0–z13. ~100K tiles, runs in minutes.
- [ ] Download via a minimal probe script (does not have to be the final
      `fetch_terrain_tiles.py` — could be inline in the plan-execution
      session).
- [ ] Measure per-tile byte sum. Project full-NA total size two ways:
      - **Area scaling.** Tile count scales with mercator-aware bbox area.
        Compute the tile-count ratio NA/CO (use `precache.py`'s tile-math
        helpers) and multiply.
      - **Per-zoom breakdown.** Compute mean bytes/tile per zoom level from
        Colorado; multiply by NA tile counts per zoom; sum.
- [ ] **Decision gate:**
      - Projected NA z0–z13 ≤ ~200 GB → proceed with z13.
      - Projected > 200 GB → fall back to z0–z12 (decision update in Results).
      - Either way, record the projection vs the eventual measured size in
        Results.

## Phase 2 — The downloader (`tools/fetch_terrain_tiles.py`)

New script in `tools/`. Mirrors the existing convention (KeyboardInterrupt →
exit 130, `print("\nInterrupted.")`, partial-stats summary).

### CLI shape

```
uv run tools/fetch_terrain_tiles.py \
  --bbox "-168,7,-52,72" \
  --zoom 0-13 \
  --output ~/terrain-tiles-lab/northamerica-terrain.mbtiles \
  --concurrency 32
```

Optional flags:
- `--region <name>` — reuse `precache.py`'s region table (colorado, conus, …)
  for sampling runs.
- `--dry-run` — print tile count + projected bytes (from a small probe
  sample) and exit. Use this for the Phase 1 size estimate.

### Behavior

- **Source URL pattern:**
  `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png`
- **Tile math:** standard slippy-map. Reuse helpers from `tools/precache.py`
  rather than reinventing.
- **Writes directly to MBTiles** (`tiles(zoom_level, tile_column, tile_row,
  tile_data)`, plus a `metadata` table — `name`, `format=png`, `bounds`,
  `minzoom`, `maxzoom`, `type=baselayer`, `description`). MBTiles uses **TMS
  Y** (flipped from XYZ Y); the script must invert before writing.
- **Concurrency:** `asyncio` + `httpx.AsyncClient` with a bounded semaphore
  (default 32). SQLite writes serialized via a single asyncio task draining
  a queue.
- **Resumability:** before fetching, `SELECT 1 FROM tiles WHERE zoom_level=?
  AND tile_column=? AND tile_row=?` — skip if present. So a Ctrl-C and
  re-run picks up where it left off.
- **Backoff:** retry on 5xx and connection errors with exponential backoff,
  give up after N attempts and log the missing tile. 404 is recorded as
  "no data here" and skipped (some high-zoom tiles legitimately don't exist
  in remote ocean / arctic areas).
- **Progress reporting:** periodic stderr line every N seconds with tiles
  fetched, bytes written, current rate, ETA per zoom level.
- **No tile transformation.** PNGs are stored as-fetched. MapLibre handles
  the Terrarium decode.

### Verification

- After completion, `pmtiles convert input.mbtiles out.pmtiles` does its own
  validation. Before that, run a quick consistency check:
  `SELECT zoom_level, COUNT(*) FROM tiles GROUP BY zoom_level` to confirm
  expected tile counts per zoom.

## Phase 3 — PMTiles packing

Single command:

```
pmtiles convert ~/terrain-tiles-lab/northamerica-terrain.mbtiles \
                ~/terrain-tiles-lab/northamerica-terrain.pmtiles
```

- Record: input MBTiles size, output PMTiles size, conversion time. The two
  are typically close in size (PMTiles is the same compressed PNGs with
  different indexing).
- Then `pmtiles show out.pmtiles` to confirm bounds, zoom range, tile count.
  Bounds should match the bbox; zoom range should be 0–13 (or 0–12 per the
  Phase 1 decision); tile count should agree with the MBTiles row count.

## Phase 4 — Flask serving

Mirror the existing OSM PMTiles route in `api/routes/tiles.py`.

- [ ] Add route `GET /tiles/terrain.pmtiles` → `send_file(path,
      conditional=True)` — same shape as the existing `/tiles/osm.pmtiles`
      route. The conditional flag enables HTTP range, which pmtiles.js
      requires.
- [ ] Resolve the path from `$GPS_TERRAIN_PMTILES_PATH` with a dev fallback
      (`~/.cache/gps-dashboard/northamerica-terrain.pmtiles`) so the dev
      laptop can serve a sample without hitting the Pi path.
- [ ] No changes to `api/tile_layers.py` (that's the raster layer registry;
      terrain is a vector-pmtiles-style range-served archive, not a raster
      proxy).
- [ ] Update `deploy/`? No — no service changes. The Flask app already runs
      under `gps-dashboard.service`; the new route ships with the next push.

## Phase 5 — Smoke test (offline-faithful)

The point is to prove the archive serves and renders before the integration
effort touches the real app. Throwaway scratch, not in git.

- [ ] Minimal HTML page in `~/terrain-tiles-lab/harness/terrain.html`:
      - Vendored MapLibre GL JS (any 5.x release; the integration will pick
        the pinned version separately).
      - Vendored `pmtiles.js` (with `addProtocol` registered).
      - A trivial style with a `raster-dem` source pointing at
        `pmtiles:///path/to/northamerica-terrain.pmtiles` and
        `encoding: 'terrarium'`.
      - `terrain: { source: 'dem', exaggeration: 1.2 }`.
      - Centered on a known high-relief region (Front Range west of Denver,
        e.g. `[-105.6, 39.7]`, zoom 11, pitch 60°).
- [ ] Confirm:
      - HTTP range requests succeed (devtools Network panel, look for 206
        responses against `/tiles/terrain.pmtiles`).
      - Terrain mesh renders without obvious banding or artifacts.
      - Mountain ranges look mountain-shaped (eyeball check — drag pitch
        slider, confirm relief is real not flat).
      - **Offline gate:** with devtools in offline mode (and dev server
        running), reload — no external requests, terrain still renders.

## Phase 6 — Ship to Pi

Same operational pattern as `northamerica.pmtiles`. The archive itself is
not in git.

```
# On the dev machine
rsync -P --partial --inplace ~/terrain-tiles-lab/northamerica-terrain.pmtiles \
  pmorgan@192.168.42.178:/mnt/nvme/tiles/northamerica-terrain.pmtiles.tmp

# Atomic rename on the Pi
ssh pmorgan@192.168.42.178 \
  "mv /mnt/nvme/tiles/northamerica-terrain.pmtiles.tmp \
      /mnt/nvme/tiles/northamerica-terrain.pmtiles"
```

The Pi link is ~10 Mbps, so an archive of ~50–150 GB takes many hours. Plan
this as overnight, not interactive.

If the dev laptop's upstream is slow, build on the NAS (`rex-nas` —
`/volume2/scratch/`, 1 Gbps to internet) and copy NAS → Pi from there
(see the OSM PMTiles plan's `copy-to-pi.sh` for the pattern).

## Phase 7 — Documentation

- [ ] Update `CLAUDE.md` "Architecture / Basemaps" section to add the
      terrain archive next to the OSM PMTiles description: where it lives,
      env var, format (Mapzen Terrarium PNG), update model (re-extract +
      atomic replace, no per-tile refresh).
- [ ] Add the env var to the Deployment section.
- [ ] Add `tools/fetch_terrain_tiles.py` invocation examples to the Commands
      section.

## Out of scope for this plan

Tracked here so the next plan picks them up cleanly:

- **Dropping the Leaflet wrapper** (`maplibre-gl-leaflet`) for pure MapLibre.
  Required for pitch / terrain controls; rewrites GPS polyline, current-fix
  marker, FAB-zoom, annotation pins, slider-driven view updates, and the
  USGS raster basemap (as a MapLibre `raster` source). Big enough to be its
  own plan.
- **Draping the GPS track on the terrain.** Needs MapLibre 5.x +
  `line-elevation-reference: ground`. Confirm vendored MapLibre version
  before assuming.
- **Pitch / terrain UI controls.** Pitch handle, terrain exaggeration
  slider, "flatten" toggle for 2D mode.
- **3D buildings.** Protomaps "light" basemap doesn't carry building
  heights; would need a schema swap or supplementary source.
- **3DEP upgrade path.** Re-build a higher-res terrain archive over chosen
  regions (Rockies / Sierras / Cascades) from USGS 3DEP 1/9 arc-second or
  LIDAR 1 m via `rio-rgbify`. Same MapLibre integration, different archive
  bytes.

## Success criteria (go/no-go)

All must hold:

1. `northamerica-terrain.pmtiles` exists, `pmtiles show` reports bounds
   matching the bbox and the expected zoom range.
2. Smoke-test harness renders 3D terrain over a known mountain region with
   no external network requests.
3. Final archive size fits within the disk budget on both dev and Pi.
4. Flask route serves with HTTP range (devtools shows 206 responses).

If 1–4 hold, the data layer is done and the next plan (frontend integration)
can start.

## Open questions / risks

- **Size projection accuracy.** Phase 1 sampling decides z13 vs z12. The
  fallback exists for a reason — don't commit to z13 disk usage without
  the sample.
- **Bucket longevity.** AWS Open Data sponsorships can end. Once we have
  the PMTiles, we don't need the bucket — treat it like the Geofabrik
  extract used for OSM: source-once, archive-forever.
- **Vertical seams at source-DEM boundaries.** Mapzen blends NED / SRTM /
  CDEM / GMTED; some boundaries are visible at extreme tilt. If a specific
  boundary looks bad in real use, that's a 3DEP-upgrade prompt for that
  region, not a blocker for the initial build.
- **Mapzen freshness.** Frozen ~2019. Elevation doesn't move, so this is
  mostly fine; LIDAR-rescanned areas may have newer data available via
  3DEP. Same upgrade-later answer.

## References

- AWS Open Data registry: https://registry.opendata.aws/terrain-tiles/
- Tile URL pattern: `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png`
- Terrarium encoding: `elevation_m = (R*256 + G + B/256) - 32768`
- MapLibre raster-dem with Terrarium:
  https://maplibre.org/maplibre-style-spec/sources/#raster-dem
- `go-pmtiles`: https://github.com/protomaps/go-pmtiles
- MBTiles spec (for the intermediate format):
  https://github.com/mapbox/mbtiles-spec/blob/master/1.3/spec.md
- Sibling plan (operational patterns to mirror):
  `docs/vector-tiles-prototype-plan.md`

## Results

_Session 1 (2026-06-16). Phases 0, 1, 2, 4, 5 closed end-to-end against a
Colorado calibration archive; Phase 6 (NA build) in progress on the NAS; Phases
3 and 7 blocked on the NA build finishing._

### Generation path: as planned

asyncio + httpx worker pool fetching from `s3://elevation-tiles-prod/terrarium/`
into MBTiles, then `pmtiles convert`. No deviation from the plan. The Mapzen
bucket served anonymous reads without authentication or throttling at the
modest concurrencies we tested (32 on the laptop, 64 on the NAS).

### Decisions settled

- **Decision #2 (zoom range): z0–z12, not z0–z13.** Pre-committed contingency
  triggered — the NA z0–z13 projection blew the 200 GB disk budget at ~369 GB.
  See "Per-tile size off by ~3×" below. z0–z12 projects to ~116 GB, sits at
  native NED 10m resolution, no interpolation.
- **Decision #6 (build location): NAS, not dev laptop.** The plan permitted the
  NAS as a fallback. Switched to it for the same reasons the vector-tiles
  effort did: 1 Gbps internet, no overnight laptop tie-up, NAS→Pi rsync is
  already the proven path. Build host: `rex-nas.rex.pmormr.com` (Debian 12,
  x86_64).
- **All other decisions** carried forward as written (Mapzen Terrarium PNG,
  single `.pmtiles` archive, NA bbox `-168,7,-52,72` matching the OSM archive).

### Measured

- **Phase 0 prereqs** — `go-pmtiles` 1.30.3 via `brew install pmtiles`
  (formula name is `pmtiles`, not `go-pmtiles`). `httpx>=0.27` added to
  `pyproject.toml`. Sanity-fetch of `terrarium/0/0/0.png` → 200 OK, 106 KB PNG.
  On the NAS: stripped Python 3.11.2 ships without `pip` or `venv`; installed
  `uv` via `https://astral.sh/uv/install.sh` and used `uv venv` to bootstrap
  the deps cleanly without touching system Python.
- **Phase 1 dry-run probes** — `--dry-run` samples 64 tiles per zoom and
  projects archive size from per-zoom mean bytes × per-zoom tile counts:
  | bbox | zoom | tiles | projected |
  |---|---|---:|---:|
  | colorado | z0–z13 | 25,616 | 2.61 GB |
  | colorado | z0–z12 | 6,618 | 727 MB (computed by subtraction) |
  | north_america | z0–z13 | 7,904,132 | **369.1 GB** ← over budget |
  | north_america | z0–z12 | 1,979,972 | **115.8 GB** |
  | north_america | z13 alone | 5,924,160 | 253.3 GB |
- **Phase 1 CO calibration (real fetch)** — `--region colorado --zoom 0-12`
  on the dev laptop at concurrency 32: 6,618 tiles, 0 errors, **739 MB on
  disk, ~50 s at 132 req/s sustained**. Projection 727 MB vs actual 739 MB
  is **within 2%** — random-sample probe is a good estimator. Per-zoom row
  counts in the resulting MBTiles match the expected tile counts exactly.
- **Phase 3 (validated on CO only this session)** — `pmtiles convert` packed
  710 MB MBTiles into a 705 MB PMTiles in 2.4 s. `pmtiles show` reports
  bounds, zoom range 0–12, tile-contents count equals addressed count
  (**dedup ratio 1.0** — every elevation tile is unique by content, no
  free compression from identical-tile RLE the way the OSM archive got).
- **Phase 4** — `GET /tiles/terrain.pmtiles` mirrors `osm.pmtiles`: HEAD
  returns 200 + `Accept-Ranges: bytes`, `Range: bytes=0-6` returns 206 +
  `Content-Range: bytes 0-6/739191761` with body `PMTiles` magic.
- **Phase 5 smoke test** — `static/dev-terrain.html` (gitignored) loads
  MapLibre + pmtiles.js, centers on Front Range west of Denver
  `[-105.6, 39.7]` at zoom 11 / pitch 60° with a `raster-dem` source
  `pmtiles:///tiles/terrain.pmtiles` + `encoding: 'terrarium'`. Renders
  mountain-shaped relief; nav/pitch interactions work; DevTools shows 206
  responses for the byte-range fetches.

### Per-tile size off by ~3× from the plan's estimate

Plan cited "~1–2 KB ocean, ~10–25 KB land, ~20–40 KB mountain" for Mapzen
Terrarium tiles. Actual sample means are **~40–100 KB across all zooms** for
both Colorado and NA bboxes — even the z0 whole-globe tile is 106 KB. The
plan's estimate came from a different terrain encoding (probably Mapbox
Terrain-RGB compressed with Mapnik). Terrarium PNGs encode high-entropy
elevation data per pixel and don't compress nearly as much.

This is the dominant reason z13 blew the budget. The z0–z12 size came in
roughly where the plan predicted, just for different reasons (smaller tile
count, not smaller bytes/tile).

### NAS disk choice matters: ~2× speedup on SSD

The NA build first started on `/volume2` (BTRFS over the UGREEN pool) at
**~53–60 req/s declining**, ETA ~10 h. The user pointed out `/volume3` is
ext4-on-SSD (mounted `nobarrier`). Switching the MBTiles output to
`/volume3/home/pmorgan/terrain-tiles-lab/out/` immediately stabilised the
fetcher at **~94–100 req/s**, ETA ~5.4 h. Source files + venv stayed on
`/volume2`. Bottleneck on the slow volume was almost certainly SQLite
fsync at WAL checkpoint boundaries — Synology BTRFS over a hybrid pool is
much higher fsync latency than ext4-on-SSD with no write barriers.

(For context the laptop run was ~132 req/s at concurrency 32 on local NVMe.
The NAS gets less throughput per worker; doubling concurrency to 64 didn't
fully close the gap, suggesting some headroom in NAS↔S3 RTT or the
Synology kernel's connection handling.)

### Detach gotcha: `nohup` alone isn't enough on Synology

First NA build attempt died when the wrapping SSH session ended — the
detached process inherited the SIGHUP. Fix:
`setsid nohup … & disown; sleep 1` so the python process lands in its own
session group and survives ssh teardown. Worth knowing for any future
long-running NAS jobs.

### Notes for the integration follow-on

- **Hillshade + 3D terrain can't share a raster-dem source.** MapLibre
  logged `You are using the same source for a hillshade layer and for 3D
  terrain. Please consider using two separate sources to improve rendering
  quality.` The smoke test ignored this because the harness only needed the
  mesh visible without a basemap. In the integration plan, give `terrain`
  and `hillshade` their own `raster-dem` sources, even if they point at
  the same PMTiles archive — MapLibre keeps them on separate GPU textures
  that way.
- **No `?refresh` for terrain.** Same as the OSM PMTiles archive: single
  immutable file, no per-tile refresh mechanism. Update means re-fetch +
  full file replacement. Since elevation doesn't move, cadence is
  effectively never for the bulk archive (the 3DEP upgrade path stays open
  for targeted regions).
- **Vendor an MVT-free MapLibre version is fine.** The smoke test used the
  already-vendored `maplibre-gl.js` from the OSM vector tiles work; the
  raster-dem source path is built into the same library, no second build.
- **The shared `tools/regions.py`** introduced for this effort is the right
  hub for future tile tooling; the next refactor candidate is the tile-math
  helpers in `tools/precache.py` (`lat_lon_to_tile`, `tiles_for_bbox`,
  `count_tiles`), which `fetch_terrain_tiles.py` currently imports through
  the path-shim that `precache.py` itself sets up. Pulling those into
  `tools/tiles.py` would let the two scripts stop importing each other's
  CLI module.

### Pending

- Phase 6 NA build still running (last check: ~50 K / 1.98 M tiles, ETA
  ~5.4 h, ~100 req/s steady on `/volume3`).
- Phase 3 `pmtiles convert` on the full NA MBTiles is then a single
  command; budget under a minute based on the CO conversion's
  ~290 MB/s throughput.
- Phase 6 rsync NAS→Pi for ~116 GB at Pi's ~10 Mbps link projects to
  ~26 hours; treat this as multi-day (mirror the OSM archive's
  `copy-to-pi.sh` pattern with `--inplace --partial` + atomic `.tmp` →
  `mv`).
- Phase 5 will be re-run against the full NA archive once it lands on the
  Pi — same harness, same checks, just over the LAN to confirm phone
  rendering performance over van WiFi.
