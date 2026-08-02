# Basemaps & Terrain

Two basemaps served two different ways, plus an elevation source for 3D rendering. A
single `maplibregl.Map` (`MapView`, `web/src/lib/map.ts`) renders everything — pure
MapLibre GL (not Leaflet) so the map can pitch and drape on terrain. The map's right
icon rail drives it via the **Map style** panel (`web/src/views/MapStyle.svelte`):
basemap switch, vector *theme* picker, terrain draping + exaggeration (off = flat 2D,
north-up; on unlocks pitch + rotate), and label *density* / minor-street-name tuning
(vector only). The
basemap's **`pois` marks** are fully owned at runtime by the composer in
`web/src/lib/labels.ts`: one function sets the layer's filter (the shared POI
category selection × per-feature `min_zoom` density gate × twin-suppression feature
ids), `icon-image`, and category-colored `icon-color`/`text-color` from the unified
icon language (`web/src/lib/icons.ts`). Icons come from a second, SDF **`poi`
multi-sprite** (`static/vendor/basemap/sprite-poi/`, built by
`tools/build_poi_sprite.py` from the vendored CC0 Maki/Temaki SVGs in
`static/vendor/poi-icons/`; SDF = one monochrome set, tinted at render time). The
raster style carries the `poi` sprite too, so overlay pins keep their icons on USGS.
Marks are tappable — the tile feature id is planetiler-encoded (`type·2⁴⁴ + osm_id`)
and round-trips to the places tier's `source_id` (codec in `icons.ts`; resolution
via `GET /api/places/lookup`).

## OSM — vector (default)

A single immutable PMTiles archive (`northamerica.pmtiles`, z0–15, ~33 GB) rendered
client-side by MapLibre GL. Flask serves it at `GET /tiles/osm.pmtiles` with HTTP range
support (`send_file(conditional=True)`); `pmtiles.js` issues range requests for the
header, directories, and tiles. Path from `$GPS_PMTILES_PATH`
(`/mnt/nvme/tiles/northamerica.pmtiles` on the Pi; dev fallback
`~/.cache/gps-dashboard/northamerica.pmtiles`). MapLibre and pmtiles are npm deps
bundled into the committed SPA build (`static/dist/`); the five theme styles +
full Noto Sans BMP glyphs + sprites (light + dark variants) are data assets under
`static/vendor/basemap/` — no CDN at runtime. Crisp overzoom past z15
is free. The archive is a persistent asset (like the DB), updated only by a full
re-extract + atomic file replace (see Archive build notes below), never per-tile —
there is no `?refresh` for vector.

**Themes.** The five style documents (`style-{light,dark,white,grayscale,black}.json`)
are *generated*, not hand-vendored: `npm run gen:styles` (in `web/`) runs
`web/scripts/generate-basemap-styles.mjs`, which emits every built-in
`@protomaps/basemaps` flavor plus local overrides — zoom-interpolated road-name sizes
(Medium face for majors), scaled shields, and a readable early floor for POI-mark text.
**Dark is the boot default** (`DEFAULT_BASEMAP_THEME`, `labels.ts`); the Map style
panel switches themes at runtime (`MapView.setVectorTheme`; per-theme style docs
fetched + cached on first use); `applyLabels` re-applies mark styling on
every style load and swaps the mark halo dark on the dark-ground themes. Regeneration
caveats: the npm package's output must target the served archive's tile schema version
(v4.x — check `pmtiles show`), and the runtime addresses layers by id (`pois`,
`roads_labels_minor`), so diff `.layers[].id` old-vs-new before committing a bump.
Dark/black themes use the vendored `sprite/dark` variant (shields, oneway arrows,
townspots); `sprite-poi` is theme-independent (SDF, tinted at runtime).

**Pitched-view tile cover.** `reinstallOverlays` sets
`setSourceTileLodParams(4, 3)` on every style (re)load — more distinct zoom levels on
screen and a bigger high-pitch tile budget, so the far field loads without a camera
nudge (no effect at pitch 0; verified to pull extra far-field detail at pitch ~78 in a
headless harness — final judgment is on-device in the van).

**Idle prefetch (raster only).** On map `idle` with a raster base active, the engine
warms the tiles the user likely needs next (`web/src/lib/prefetch.ts`, pure tile math +
vitest): parents of the on-screen cover at tileZoom−1..−3 (the zoom-out gesture; USGS
is a 256px source, so tileZoom = round(display zoom + 1)) plus a one-tile pan ring,
capped ~48/settle, deduped per session. Every GET goes through the tile proxy, so
online browsing doubles as offline precache. Vector/DEM are excluded on purpose: plain
GETs can't warm a byte-ranged PMTiles archive, and the in-session MapLibre cache
already covers vector zoom-out.

**Trap:** `map.ts` loads the style as an object and absolutizes `sprite`/`glyphs`
against `location.origin` (MapLibre rejects a relative sprite); the pmtiles source URL
stays root-relative.

**Tile-data floor (measured 2026-07-15):** the archive's `pois` source-layer carries
features only **one zoom ahead** of the tile's own zoom (z10 tiles hold `min_zoom` ≤ 11,
z12 → ≤ 13, …). The density slider's `-1` offset therefore already reveals everything
physically present at browse zooms; more-negative offsets only matter near the z15
max-zoom. "More icons when zoomed out" comes from the places-tier overlay instead
(API-read, not tile-bound): the same slider shifts the overlay's rank×zoom pin gate
(places.md).

## USGS — raster

Flask proxies USGS Topo tiles when online and caches to
`$GPS_TILE_CACHE_DIR/usgs/{z}/{x}/{y}.png` (`/mnt/nvme/cache/tiles` on the Pi; dev
fallback `~/.cache/gps-dashboard/tiles/`). Serves from cache offline; 503 if uncached
and offline. Route `GET /tiles/<layer>/<z>/<x>/<y>.png`; unknown layer or z past the
layer's max → 400. `api/tile_layers.py` is the raster layer registry (USGS only),
imported by both the route and `precache.py`. ETags live in sidecar files
(`<layer>/{z}/{x}/{y}.etag`); `?refresh=1` fires a background `If-None-Match` GET per
tile and silently updates the cache. The refresh toggle in the Map style panel enables
refresh mode — raster only, disabled while the vector base is selected. Tile writes are atomic
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
on the Pi (rsync → `.tmp`, `ssh mv` → final). **In production nginx direct-serves these two
`.pmtiles` archives** (`location = /tiles/osm.pmtiles` / `terrain.pmtiles` →
`deploy/gps-dashboard.nginx.conf`) rather than waitress, so the replaced file **must be
world-readable** (`chmod o+r`; the nginx workers run as `www-data` — a `600` archive 403s).
Local dev without nginx still serves them through Flask, so this only bites on the Pi.

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
