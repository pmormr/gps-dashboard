# Basemaps & Terrain

Two basemaps served two different ways, plus an elevation source for 3D rendering. A
single `maplibregl.Map` (`MapView`, `static/js/map.js`) renders everything — pure
MapLibre GL (not Leaflet) so the map can pitch and drape on terrain. A layer dropdown
in the tab bar switches the active basemap; the 🏔 3D panel toggles terrain draping and
exaggeration (off = flat 2D, north-up; on unlocks pitch + rotate). The ⚙ Labels panel
(vector only, `static/js/labels.js`) tunes POI categories, label density, and
minor-street-name visibility by driving the MapLibre map directly.

## OSM — vector (default)

A single immutable PMTiles archive (`northamerica.pmtiles`, z0–15, ~33 GB) rendered
client-side by MapLibre GL. Flask serves it at `GET /tiles/osm.pmtiles` with HTTP range
support (`send_file(conditional=True)`); `pmtiles.js` issues range requests for the
header, directories, and tiles. Path from `$GPS_PMTILES_PATH`
(`/mnt/nvme/tiles/northamerica.pmtiles` on the Pi; dev fallback
`~/.cache/gps-dashboard/northamerica.pmtiles`). The MapLibre lib, pmtiles.js, and the
Protomaps "light" style + full Noto Sans BMP glyphs + sprite are vendored under
`static/vendor/{maplibre,pmtiles,basemap}` — no CDN at runtime. Crisp overzoom past z15
is free. The archive is a persistent asset (like the DB), updated only by a full
re-extract + atomic file replace (see Archive build notes below), never per-tile —
there is no `?refresh` for vector.

**Trap:** `map.js` loads the style as an object and absolutizes `sprite`/`glyphs`
against `location.origin` (MapLibre rejects a relative sprite); the pmtiles source URL
stays root-relative.

## USGS — raster

Flask proxies USGS Topo tiles when online and caches to
`$GPS_TILE_CACHE_DIR/usgs/{z}/{x}/{y}.png` (`/mnt/nvme/cache/tiles` on the Pi; dev
fallback `~/.cache/gps-dashboard/tiles/`). Serves from cache offline; 503 if uncached
and offline. Route `GET /tiles/<layer>/<z>/<x>/<y>.png`; unknown layer or z past the
layer's max → 400. `api/tile_layers.py` is the raster layer registry (USGS only),
imported by both the route and `precache.py`. ETags live in sidecar files
(`<layer>/{z}/{x}/{y}.etag`); `?refresh=1` fires a background `If-None-Match` GET per
tile and silently updates the cache. The "↻" checkbox in the tab bar enables refresh
mode — raster only, disabled while the vector base is selected. Tile writes are atomic
(thread-unique `.tmp` then `replace`), so a crash mid-write can never leave a torn PNG.

## Terrain — Mapzen Terrarium PNG, served as PMTiles

Not a basemap of its own; an elevation source MapLibre reads via a `raster-dem` style
source with `encoding: 'terrarium'`. A single immutable PMTiles archive
(`northamerica-terrain.pmtiles`, z0–12, ~105 GB) covering the same NA bbox as the OSM
archive. Flask serves it at `GET /tiles/terrain.pmtiles` with byte-range support — same
`send_file(conditional=True)` shape as `osm.pmtiles`. Path from
`$GPS_TERRAIN_PMTILES_PATH` (`/mnt/nvme/tiles/northamerica-terrain.pmtiles` on the Pi;
dev fallback `~/.cache/gps-dashboard/northamerica-terrain.pmtiles`). Each PNG encodes
elevation in meters per pixel as `(R*256 + G + B/256) - 32768`. Source: AWS Open Data
`s3://elevation-tiles-prod/terrarium/` (CONUS built from USGS NED 10m, ~z12 native;
global coverage outside CONUS). Update model identical to OSM: full re-fetch + atomic
replace, no `?refresh`.

The DEM feeds two `raster-dem` sources — one for `setTerrain` (the mesh), one for an
optional hillshade layer inserted below the vector labels (USGS topo already bakes in
relief, so hillshade is vector-only). The GPS track and annotation ranges are plain
GeoJSON `line` layers, which MapLibre drapes onto the mesh automatically via
render-to-texture.

**Eliminated pathway:** no special draping property is needed — `line-elevation-reference`
is a Mapbox-only feature and is not used.

## Tile tooling

`tools/precache.py` pre-downloads raster tiles (USGS only) for a bbox + zoom range. The
region table is in `tools/regions.py` (frozen `Region` dataclass, shared with
`fetch_terrain_tiles.py`). Practical zoom z8–z14 for USGS. Source modes: `--region`,
`--bbox`, `--local` (bbox around the current GPS fix, `--radius` km, default 50).

`tools/fetch_terrain_tiles.py` downloads Mapzen Terrarium PNGs into an MBTiles archive
(asyncio + httpx worker pool, per-zoom resume, TMS-Y inversion on write). `pmtiles
convert` then packs the MBTiles into the served PMTiles archive. Build off-Pi (dev
laptop or NAS) and ship via rsync; never build on the Pi.

## Archive build notes

Both archives are built **off-Pi** (dev laptop or the `rex-nas` NAS) and atomic-replaced
on the Pi (rsync → `.tmp`, `ssh mv` → final).

**Vector OSM** — a Protomaps **extract** of a dated planet build (not planetiler),
z0–15, NA bbox `-168,7,-52,72`. Identical-tile RLE gives real free compression here.

**Terrain DEM** — Mapzen Terrarium PNG → MBTiles (`fetch_terrain_tiles.py`) → `pmtiles
convert`, same NA bbox.
- **z0–z12, not z13.** NA z13 projects to ~369 GB (over the ~200 GB budget); z12 is
  ~116 GB at native NED 10m resolution — z13 buys only interpolation.
- **Terrarium PNGs are ~40–100 KB/tile** (high-entropy elevation, ~3× the naive
  estimate) and **don't dedup** (content ratio ~1.0), unlike the OSM archive — so the
  zoom cap, not byte savings, is the size lever.
- **Build-host gotchas:** write the MBTiles to SSD/ext4, not BTRFS-over-hybrid — SQLite
  fsync at WAL checkpoints made the NAS ~2× slower on BTRFS. Detach long NAS jobs with
  `setsid nohup … & disown` (bare `nohup` inherits SIGHUP and dies on ssh teardown).
  `pmtiles` installs via `brew install pmtiles` (formula is `pmtiles`, not `go-pmtiles`).
