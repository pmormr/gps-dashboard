# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A GPS history browser for a Raspberry Pi installed in a van, serving a local network (LAN) that is frequently off-grid with no internet access. The Pi logs GPS data continuously; this app is the interface for reviewing, tagging, and analyzing that data.

Users connect via phone or laptop over the van's WiFi. No authentication is required — the LAN is trusted.

The full implementation plan is at `docs/plan.md`.

## Deployment

Two systemd services run on the Pi: `gps-logger` (writes GPS data) and `gps-dashboard` (serves the web app). Both are managed via a bare git repo with a post-receive hook.

```bash
# Commit and push to both GitHub and Pi in one step (preferred)
git push all main
```

The hook runs `uv sync`, then restarts services based on what changed. It always restarts `gps-dashboard` and (if enabled) `mqtt-ingest` and `gps-processor`. When any `deploy/` file changed it reinstalls all five unit files into `/etc/systemd/system/` and `daemon-reload`s first — so editing a service's env var (e.g. `GPS_TERRAIN_PMTILES_PATH`) deploys on push with no manual `systemctl` step. `gps-logger` restarts only if `logger/` (or its unit) changed, to avoid GPS data gaps; `mosquitto`/`sensor-bme680` restart only on their own config/source changes. The `pi` remote points to `pmorgan@192.168.42.178:/mnt/nvme/gps-dashboard.git`.

App files live on an NVMe drive mounted at `/mnt/nvme`:
- `/mnt/nvme/gps-dashboard.git` — bare repo (deploy target)
- `/mnt/nvme/gps-dashboard` — working tree (overwritten by deploys)
- `/mnt/nvme/data/gps_history.db` — database (persists across deploys)
- `/mnt/nvme/cache/tiles/` — raster (USGS) tile cache (persists across deploys)
- `/mnt/nvme/tiles/northamerica.pmtiles` — vector OSM basemap archive, ~33 GB (persists across deploys)
- `/mnt/nvme/tiles/northamerica-terrain.pmtiles` — terrain (Mapzen Terrarium) PMTiles archive, ~105 GB (persists across deploys)

**Never commit directly on the Pi.** All commits go local → push to both remotes. Direct Pi commits cause history divergence requiring force-pushes to fix.

```bash
# Add remotes if missing
git remote add pi pmorgan@192.168.42.178:/mnt/nvme/gps-dashboard.git
git remote add all https://github.com/pmormr/gps-dashboard.git
git remote set-url --add all pmorgan@192.168.42.178:/mnt/nvme/gps-dashboard.git

# Logs and status
ssh pmorgan@192.168.42.178 "journalctl -u gps-dashboard -f"
ssh pmorgan@192.168.42.178 "journalctl -u gps-logger -f"
ssh pmorgan@192.168.42.178 "sudo systemctl status gps-dashboard gps-logger"
```

App runs at `http://192.168.42.178:5000`.

## Architecture

### Processes

- **Logger** (`logger/gps_logger.py`) — standalone script, no Flask. Reads from gpsd via TCP socket on `localhost:2947`. The only writer of raw (`gps_points` + `receiver_metadata`); position writes are motion-gated (5 Hz moving / ~1 Hz parked).
- **Processor** (`processor/gps_processor.py`) — standalone, no Flask. Tails raw `gps_points` by a persisted id cursor and derives the processed tier (`track_points` + `track_events`) the frontend reads. Idempotent and fully rebuildable from raw; never writes raw. Enabled-gated service. (Phase 2: copy-through skeleton; the denoise filter lands in Phase 3 — see `docs/gps-denoise-plan.md`.)
- **Web app** (`api/app.py`) — Flask, read-heavy. Serves the frontend, JSON API, tile proxy, and status pages.

### Data Model

SQLite (`gps_history.db`). Core GPS tables:

- `gps_points(id, timestamp, lat, lon, speed, altitude, track, epx, epy, epv, eps, climb, mode)` — raw append-only stream; the per-fix accuracy/quality columns feed the denoise processor. `timestamp` is fixed-width ms UTC (`canonical_timestamp`), uniform across all tiers.
- `annotations(id, name, start_time, end_time, notes)` — pure metadata; no foreign keys. `end_time` nullable: NULL = point-in-time bookmark; non-NULL = range, whose points come from `WHERE timestamp BETWEEN start_time AND end_time` against `gps_points`. (Was `trips` pre-2026-06; renamed in `_maybe_rename_trips_to_annotations`.)
- `marks(key, timestamp)` — two rows max (`start`, `end`); persists live range-construction timestamps across restarts.

Processed/denoise tier — derived from raw by `gps-processor`, fully rebuildable (see `docs/gps-denoise-plan.md`):

- `track_points(...)` — the denoised/simplified points the frontend reads (`kind` `track`|`stop`, `n_raw`, `importance`, `accuracy`, stop `dwell_*`/`radius`, `src_raw_id`). Phase 2 copies raw through 1:1; Phase 3 collapses stops + simplifies moving segments.
- `track_events(...)` — processor-emitted events (stop start/end, mode transitions, …); distinct from the user-curated `annotations`.
- `receiver_metadata(id, timestamp, hdop, vdop, pdop, nsat_used, nsat_seen)` — SKY-sourced DOP + sat counts, written by the logger on a ~5 s throttle; standalone telemetry, not joined into the position path.
- `processing_state(key, value)` — the processor's `last_committed_raw_id` cursor.

The same DB also holds the sensor-platform tables (`sensors`, `bme680_readings`, `alarm_rules`, `alarm_events`) — see the Sensor Platform section below.

### API Endpoints

- `GET /api/points?start=&end=&limit=` — points for a time range (default limit 5000, max 20000)
- `GET /api/points/latest` — single most-recent point
- `GET /api/annotations` — list every annotation; `point_count` is NULL for point bookmarks, integer for ranges
- `POST /api/annotations` — create annotation; omit (or pass null) `end_time` for a point bookmark
- `PATCH /api/annotations/:id` — edit name, notes, or bounds (including transitioning point↔range)
- `DELETE /api/annotations/:id`
- `POST /api/annotations/mark` — upsert `start` or `end` mark with current UTC time
- `GET /tiles/osm.pmtiles` — vector OSM basemap (single PMTiles archive) served with HTTP range support
- `GET /tiles/terrain.pmtiles` — terrain DEM (Mapzen Terrarium PNGs in a PMTiles archive) served with HTTP range support; read client-side by MapLibre via `raster-dem` + `encoding: 'terrarium'`
- `GET /tiles/<layer>/{z}/{x}/{y}.png` — raster tile proxy/cache (USGS); `?refresh=1` serves from cache and fires a background ETag-conditional GET, updating the cache if the tile changed
- `GET /api/sensors` — sensor registry, each row with its latest reading embedded
- `GET /api/sensors/:id/readings?start=&end=&limit=` — reading history for the trend chart (defaults to the trailing 24h)
- `GET /gpsd` — read-only gpsd status page
- `GET /ntp` — read-only NTP/chrony status page
- `GET /sensors` — sensor viewer (current values + trend charts)

### Frontend

Separate files in `static/` and `templates/`. All JS/CSS vendored in `static/vendor/` — no CDN calls at runtime. Mobile-first (primary client is a phone browser).

One map-centric view at `/`:
- **Time picker** (`TimePicker`, `static/js/timepicker.js`) — Graylog-style. Modes Last / Around / From→To with anchor + window state; preset chips (15m/1h/6h/24h/7d/30d) collapse to Live + Last. A Live flag pins the anchor to `now()` and re-fetches every 30s.
- **Sub-range slider** (`noUiSlider`) — zooms inside the loaded window. The trail polyline + map-fit follow the slider's selection; in live re-fetches `fitBounds` is skipped so the view doesn't jerk.
- **Annotations drawer** — right-edge drawer on desktop, bottom sheet on mobile, toggled from the tab bar. Lists points + ranges; click jumps the picker (range → `range` mode, point → `around` mode keeping current window) and pans to the nearest fix. Map overlays: cyan polylines for in-window ranges, amber pins for in-window points; matching bands + ticks on the slider.
- **Creation** — "Create Range" uses the slider's `[lo, hi]` (≥2 points); "Drop Pin" captures the slider's `hi` handle (or `now` in live).
- **Bucketing** — `Timeline.bucketFor(spanMs)` tiers the `?bucket=` param: ≤24h full detail, ≤7d 30s, ≤30d 5min, longer 30min.
- **Other map controls** — ⊕ FAB zooms to the most recent GPS fix; the ⚙ Labels panel (vector basemap) tunes POI categories, label density, and minor-street-name visibility; the 🏔 3D panel toggles terrain draping and sets exaggeration (off = flat 2D, north-up; on unlocks pitch + rotate).

Three standalone pages:
- `/gpsd` — gpsd service state, fix mode, satellite count, latest coordinates, pass/fail indicators
- `/ntp` — chrony sync status, stratum, offset, GPS/PPS source state, LAN server status
- `/sensors` — per-sensor current values + trend charts. JS-driven (`static/js/sensors.js`), polling `/api/sensors` and `/api/sensors/:id/readings` every 30s, charting with vendored uPlot. Reads from the logged DB — no live broker needed — so it works regardless of the broker's websockets support. Range buttons (1h/6h/24h/7d) and a liveness dot per sensor (online/stale/offline from the registry).

`/gpsd` and `/ntp` auto-refresh every 30 seconds via `<meta refresh>`; `/sensors` polls in place (a full reload would drop chart state).

### Basemaps & Terrain: Vector OSM + Raster USGS + Terrain DEM

Two basemaps, served two different ways, plus an elevation source for 3D rendering. A single `maplibregl.Map` (`MapView` in `static/js/map.js`) renders the view — Leaflet was dropped for pure MapLibre GL so the map can pitch and drape on terrain. A layer dropdown in the tab bar switches the active basemap; a 🏔 3D panel toggles terrain draping (see Terrain below).

**OSM — vector (default).** A single immutable PMTiles archive (`northamerica.pmtiles`, z0–15, ~33 GB) rendered client-side by MapLibre GL. Flask serves the archive at `GET /tiles/osm.pmtiles` with HTTP range support (`send_file(conditional=True)`); `pmtiles.js` issues range requests for the header, directories, and tiles. Path from `$GPS_PMTILES_PATH` (`/mnt/nvme/tiles/northamerica.pmtiles` on the Pi; dev fallback `~/.cache/gps-dashboard/northamerica.pmtiles`). The MapLibre lib, pmtiles.js, and the Protomaps "light" style + full Noto Sans BMP glyphs + sprite are vendored under `static/vendor/{maplibre,pmtiles,basemap}` — no CDN at runtime. Crisp overzoom past z15 is free. The archive is a persistent asset (like the DB), updated only by a full re-extract + atomic file replace (see `docs/vector-tiles-prototype-plan.md`), never per-tile — there is no `?refresh` for vector. `map.js` loads the style as an object and absolutizes `sprite`/`glyphs` against `location.origin` (MapLibre rejects a relative sprite); the pmtiles source URL stays root-relative.

**USGS — raster.** Flask proxies USGS Topo tiles when online and caches to `$GPS_TILE_CACHE_DIR/usgs/{z}/{x}/{y}.png` (`/mnt/nvme/cache/tiles` on the Pi; dev fallback `~/.cache/gps-dashboard/tiles/`). Serves from cache offline; 503 if uncached and offline. Route shape `GET /tiles/<layer>/<z>/<x>/<y>.png`; unknown layer or z past the layer's max → 400. `api/tile_layers.py` is the raster layer registry (USGS only now), imported by both the route and `precache.py`. ETags live in sidecar files (`<layer>/{z}/{x}/{y}.etag`); `?refresh=1` fires a background `If-None-Match` GET per tile and silently updates the cache. The "↻" checkbox in the tab bar enables refresh mode — raster only, disabled while the vector base is selected. Tile writes are atomic (thread-unique `.tmp` then `replace`), so a crash mid-write can never leave a torn PNG.

**Terrain — Mapzen Terrarium PNG, served as PMTiles.** Not a basemap of its own; an elevation source MapLibre reads via a `raster-dem` style source with `encoding: 'terrarium'`. A single immutable PMTiles archive (`northamerica-terrain.pmtiles`, z0–12, ~105 GB) covering the same NA bbox as the OSM archive. Flask serves it at `GET /tiles/terrain.pmtiles` with byte-range support — same `send_file(conditional=True)` shape as `osm.pmtiles`. Path from `$GPS_TERRAIN_PMTILES_PATH` (`/mnt/nvme/tiles/northamerica-terrain.pmtiles` on the Pi; dev fallback `~/.cache/gps-dashboard/northamerica-terrain.pmtiles`). Each PNG tile encodes elevation in meters per pixel as `(R*256 + G + B/256) - 32768`. Source: AWS Open Data `s3://elevation-tiles-prod/terrarium/` (CONUS layer is built from USGS NED 10m, ~z12 native resolution; global coverage outside CONUS). Update model identical to OSM: full re-fetch + atomic replace, no `?refresh`. The 🏔 3D panel toggle drapes the active basemap (vector OSM or USGS) on the mesh and unlocks pitch + rotate; default load is flat 2D, north-up. The DEM feeds two `raster-dem` sources — one for `setTerrain` (the mesh), one for an optional hillshade layer inserted below the vector labels (USGS topo already bakes in relief, so hillshade is vector-only). The GPS track and annotation ranges are plain GeoJSON `line` layers, which MapLibre drapes onto the mesh automatically via render-to-texture — no special draping property needed (`line-elevation-reference` is a Mapbox-only feature and is not used). See `docs/terrain-integration-plan.md` for the integration and `docs/terrain-tiles-plan.md` for the archive build.

A collapsible "⚙ Labels" panel (vector only) tunes POI categories, label density, and minor-street-name visibility by driving the MapLibre map directly (`static/js/labels.js`).

`tools/precache.py` pre-downloads raster tiles (USGS only) for a bbox + zoom range. Region table is in `tools/regions.py` (frozen `Region` dataclass, shared with `fetch_terrain_tiles.py`). Practical zoom z8–z14 for USGS. Source modes: `--region`, `--bbox`, and `--local` (bbox around the current GPS fix, configurable `--radius` km, default 50).

`tools/fetch_terrain_tiles.py` downloads Mapzen Terrarium PNGs into an MBTiles archive (asyncio + httpx worker pool, per-zoom resume, TMS-Y inversion on write). `pmtiles convert` then packs the MBTiles into the served PMTiles archive. Build off-Pi (dev laptop or NAS) and ship via rsync; never build on the Pi.

### GPS Logger Detail

Bypasses the Python `gps` library in favor of a direct TCP socket to gpsd on `localhost:2947`. Sends `?WATCH={"enable":true,"json":true}\n`, parses TPV JSON records. Throttles DB writes to one point per 5s. Reconnects automatically on failure with 5s backoff.

Two layers of stall detection: a 30s socket timeout catches a fully frozen gpsd (no bytes at all), and a staleness watchdog forces a reconnect if no valid fix is seen for 120s *while data is still flowing* — the case the socket timeout misses, since gpsd keeps emitting SKY/no-fix TPV. Every 60s it logs a heartbeat with points written, current fix mode, age of the last write, and a breakdown of dropped records by reason (no_fix, no_latlon, bad_range, null_island, stale_time, throttled, json_err), so a silent stall names its own cause in the journal.

### gpsd & NTP Setup

Setup and validation are handled by CLI scripts in `tools/`, not through the web UI. Service config templates live in `deploy/`.

- `tools/gpsd_setup.py` — interactive: detects devices, writes `/etc/default/gpsd`, restarts gpsd. For USB serial devices (ttyACM*/ttyUSB*), reads VID/PID via udevadm and offers to install the udev rule and switch to `/dev/gps0`. After restart, polls until gpsd is active and a TPV fix (mode ≥ 2) is received (up to 90s) before running validation.
- `deploy/99-gps-dongle.rules` — **legacy USB dongle only** (not the current serial GPS). udev rule that pins the u-blox dongle (VID 1546, PID 01a7) to `/dev/gps0` and notifies gpsd via `gpsdctl add` on every plug-in, so gpsd re-attaches whenever the dongle re-enumerates. `gpsd_setup.py` installs it for USB devices. Manual install: `sudo cp deploy/99-gps-dongle.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger`.
- `tools/gpsd_validate.py` — checks service, device, fix, data flow; prints PASS/FAIL per check
- `tools/ntp_setup.py` — interactive: configures chrony with GPS SHM source, optional PPS; enables Pi as LAN NTP server
- `tools/ntp_validate.py` — checks chrony sync, GPS/PPS source, stratum, LAN serving

Two chrony config templates:
- `deploy/chrony-gps-pps.conf` — **current**: serial GPS with PPS (sub-microsecond accuracy), stratum 1
- `deploy/chrony-gps-only.conf` — legacy USB dongle, no PPS (~100ms accuracy), stratum 10

### Sensor Platform (MQTT)

A second data stream beyond GPS: environmental sensors ingested over a local mosquitto MQTT bus into the **same** SQLite DB, for GPS↔sensor correlation. Broker + ingest + the first remote node are live — a BME680 on an ESPHome ESP32-C6 (`firmware/cabin-bme680.yaml`) running Bosch BSEC2 for a calibrated IAQ index, publishing to `sensors/cabin/bme680`. The `/sensors` page (current values + trend charts) reads the ingested data straight from the DB — see the Frontend section. *Live* (push) browser readouts via MQTT-over-WS and alarms are still planned (the WS transport is blocked on the broker; the DB-backed viewer sidesteps it). GPS logging is untouched and stays off the bus. See **`.claude/modules/sensors.md`** for the architecture and **`docs/sensor-platform-plan.md`** for the roadmap.

### Project Structure

```
gps-dashboard/
├── api/
│   ├── app.py
│   ├── db.py
│   ├── tile_layers.py
│   └── routes/
│       ├── points.py
│       ├── annotations.py
│       ├── tiles.py
│       ├── sensors.py          # /sensors page + /api/sensors[/<id>/readings]
│       ├── status_gpsd.py
│       └── status_ntp.py
├── logger/
│   └── gps_logger.py
├── processor/                  # processed-tier deriver (tails raw → track_points/track_events)
│   └── gps_processor.py
├── sensors/                    # Pi-side reader / --fake harness (BME680 now lives on an ESP32 node)
│   └── bme680.py
├── mqttbus/                    # broker-side consumers + shared MQTT helpers
│   ├── topics.py
│   ├── client.py
│   └── ingest.py
├── firmware/                   # ESPHome configs for remote ESP32 sensor nodes
│   └── cabin-bme680.yaml       # XIAO ESP32-C6 + BME680 (BSEC2 IAQ)
├── static/
│   ├── css/app.css
│   ├── img/tile-error.png
│   ├── js/
│   │   ├── api.js, app.js, geo.js, map.js, labels.js, timeline.js, annotations.js
│   │   └── sensors.js      # /sensors viewer (current values + uPlot charts)
│   └── vendor/
│       ├── leaflet/        # unused — deleted after Phase 8 validation
│       ├── nouislider/
│       ├── maplibre/       # maplibre-gl (leaflet-maplibre-gl.js here is unused, pending deletion)
│       ├── pmtiles/        # pmtiles.js range reader
│       ├── uplot/          # uPlot time-series charts (sensor trends)
│       └── basemap/        # Protomaps style.json + glyphs + sprite
├── templates/
│   ├── index.html
│   ├── gpsd.html
│   ├── ntp.html
│   └── sensors.html
├── tools/
│   ├── precache.py
│   ├── fetch_terrain_tiles.py  # Mapzen Terrarium → MBTiles (asyncio+httpx)
│   ├── regions.py              # shared Region dataclass + REGIONS table
│   ├── gpsd_setup.py
│   ├── gpsd_validate.py
│   ├── ntp_setup.py
│   └── ntp_validate.py
├── deploy/
│   ├── gps-dashboard.service
│   ├── gps-logger.service
│   ├── gps-processor.service
│   ├── mosquitto.conf
│   ├── mqtt-ingest.service
│   ├── sensor-bme680.service
│   ├── chrony-gps-only.conf
│   ├── chrony-gps-pps.conf
│   └── 99-gps-dongle.rules
├── docs/
│   ├── plan.md
│   ├── gps-denoise-plan.md
│   ├── sensor-platform-plan.md
│   ├── terrain-integration-plan.md
│   ├── terrain-tiles-plan.md
│   └── vector-tiles-prototype-plan.md
└── pyproject.toml
```

## Hardware Notes

Current GPS hardware: a u-blox **NEO-M9N** module (4-constellation: GPS + GLONASS + Galileo + BeiDou, plus SBAS/QZSS; firmware SPG 4.04, PROTVER 32.01) wired to the Raspberry Pi (CM5) GPIO header. gpsd reads the module as **UBX binary** (not NMEA) on the primary header UART `/dev/ttyAMA0` at 38400 baud — gpsd auto-configures the M9N on attach (NMEA off, UBX NAV-PVT/SAT/DOP on), so TPV carries per-fix accuracy (`epx`/`epy`) and `SKY` is fully populated. The module's TIMEPULSE is wired to GPIO 4 and read via the `pps-gpio` overlay → `/dev/pps0`. gpsd and the logger both reference `/dev/ttyAMA0`. NTP runs in GPS+PPS mode (chrony stratum 1, sub-microsecond accuracy via PPS).

(gpsd also exposes a phantom `/dev/pps1` from attaching the PPS line discipline to the UART; nothing is wired to it. Chrony only uses `/dev/pps0`.)

The module runs at 38400 — its factory default — set via `GPSD_OPTIONS="-n -s 38400"` in `/etc/default/gpsd`. The rule is "match the module's reset-default baud rate," not the literal number: an earlier attempt to drive the previous module at 115200 was lost when its config-backup power drained (cable borrowed mid-trip), reverting it to its factory default on the next reboot while gpsd kept forcing 115200 — gpsd then silently received nothing and the logger stalled invisibly for days. Keeping gpsd pointed at the module's reset default means a power loss can't desync them. PPS, not baud, drives timing precision, so the headline number is irrelevant — 38400 comfortably carries the **5 Hz UBX** nav stream from all four constellations. The nav rate (`CFG-RATE-MEAS`) is set to 200 ms / 5 Hz and persisted to flash; gpsd never *forces* a rate (unlike baud), so a flash revert to the 1 Hz factory default is graceful, not a silent stall.

Legacy hardware:
- The immediately previous module was a serial GPS at 9600 baud (its factory default); the M9N replaces it on the same UART and same GPIO 4 PPS pin, just at a higher baud rate.
- Before that, a u-blox 7 USB dongle (VID 1546, PID 01a7) pinned to `/dev/gps0` via `deploy/99-gps-dongle.rules` and run GPS-only (stratum 10, ~100ms). That udev rule and the `/dev/gps0` path apply only to the USB dongle, not the current serial GPS.

## Tool Scripts

All scripts in `tools/` must handle `KeyboardInterrupt` gracefully — print `"\nInterrupted."` and exit with code 130. Never let Ctrl+C produce a traceback. For scripts using `ThreadPoolExecutor`, catch `KeyboardInterrupt` inside the `as_completed` loop, cancel pending futures, print partial stats, and exit 130.

## Commands

```bash
# Install dependencies
uv sync

# Run the web app locally
uv run api/app.py

# Pre-cache tiles for a region
uv run tools/precache.py --region colorado --zoom 8-15
uv run tools/precache.py --layer usgs --region colorado --zoom 8-14
uv run tools/precache.py --bbox "-109.05,36.99,-102.04,41.00" --zoom 8-15
uv run tools/precache.py --local --zoom 8-15          # bbox around current GPS position
uv run tools/precache.py --local --radius 100 --zoom 8-15
uv run tools/precache.py --list-regions

# Build the terrain DEM archive (Mapzen Terrarium PNG → MBTiles → PMTiles).
# Run on dev laptop or NAS, never on the Pi.
uv run tools/fetch_terrain_tiles.py --dry-run --region north_america --zoom 0-12
uv run tools/fetch_terrain_tiles.py --region north_america --zoom 0-12 \
  --concurrency 64 --yes -o ~/terrain-tiles-lab/northamerica-terrain.mbtiles
pmtiles convert ~/terrain-tiles-lab/northamerica-terrain.mbtiles \
                ~/terrain-tiles-lab/northamerica-terrain.pmtiles
# Then atomic-replace on the Pi: rsync → .tmp, ssh mv .tmp → final.

# gpsd setup and validation (run on Pi)
uv run tools/gpsd_setup.py
uv run tools/gpsd_validate.py

# NTP setup and validation (run on Pi)
uv run tools/ntp_setup.py
uv run tools/ntp_validate.py

# Sensor pipeline (MQTT — needs a broker; PYTHONPATH set so scripts find the packages)
PYTHONPATH=. uv run mqttbus/ingest.py                       # ingest subscriber
PYTHONPATH=. uv run sensors/bme680.py --fake --node cabin   # fake publisher — pipeline test harness
PYTHONPATH=. uv run sensors/bme680.py --node cabin          # (legacy) Pi-attached I2C BME680; the live BME680 is the ESPHome node

# Inspect the database
sqlite3 "$GPS_DB_PATH" "SELECT * FROM gps_points ORDER BY id DESC LIMIT 10;"
sqlite3 "$GPS_DB_PATH" "SELECT * FROM annotations;"
```

No test suite or linter is configured.

## Offline Constraint

All runtime dependencies must work without internet. When adding new frontend libraries, vendor them into `static/vendor/`. Python packages install from `uv.lock` at deploy time — no network needed after `uv sync`. The vector OSM basemap renders fully offline (vendored MapLibre/pmtiles libs + the local PMTiles archive); USGS raster renders from its on-disk cache, and the tile proxy only reaches upstream when online.

Development happens with internet available. Building the vector PMTiles archive, pre-caching USGS tiles, and vendoring assets are intentional prep steps before going off-grid.
