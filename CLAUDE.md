# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A GPS history browser for a Raspberry Pi installed in a van, serving a local network (LAN) that is frequently off-grid with no internet access. The Pi logs GPS data continuously; this app is the interface for reviewing, tagging, and analyzing that data.

Users connect via phone or laptop over the van's WiFi. No authentication is required — the LAN is trusted.

## Documentation layout

This file is the architectural map and router: base architecture + pointers. Landed subsystem detail lives in `.claude/modules/` (`frontend`, `basemaps`, `hardware`, `processor`, `sensors`); **active/in-flight** plans live in `plans/` (`obd-platform`, `motion-imu`, `sensor-ideas`). Keep all of it to **current state, critical traps, and eliminated pathways** — the back-and-forth that produced a decision belongs in git history, not here. When a plan lands, fold its durable bits into the relevant module and drop the plan.

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
- **Processor** (`processor/gps_processor.py`) — standalone, no Flask. Tails raw `gps_points` by a persisted id cursor and derives the processed tier (`track_points` + `track_events`) the frontend reads. Idempotent and fully rebuildable from raw; never writes raw. Enabled-gated service. (Phase 3: online denoise — software static-hold stops (accuracy-weighted mean) + Reumann–Witkam moving simplification + per-fix accuracy gating, emitting `stop_start`/`stop_end` events; the cursor advances only to finalized emits, so an open dwell and the open moving segment stay provisional. Phase 4: the frontend now reads it — `/api/points` serves `track_points`. See `.claude/modules/processor.md`.)
- **Web app** (`api/app.py`) — Flask, read-heavy. Serves the frontend, JSON API, tile proxy, and status pages.

### Data Model

SQLite (`gps_history.db`). Core GPS tables:

- `gps_points(id, timestamp, lat, lon, speed, altitude, track, epx, epy, epv, eps, climb, mode)` — raw append-only stream; the per-fix accuracy/quality columns feed the denoise processor. `timestamp` is fixed-width ms UTC (`canonical_timestamp`), uniform across all tiers.
- `annotations(id, name, start_time, end_time, notes)` — pure metadata; no foreign keys. `end_time` nullable: NULL = point-in-time bookmark; non-NULL = range, whose points come from `WHERE timestamp BETWEEN start_time AND end_time` against `gps_points`. (Was `trips` pre-2026-06; renamed in `_maybe_rename_trips_to_annotations`.)
- `marks(key, timestamp)` — two rows max (`start`, `end`); persists live range-construction timestamps across restarts.

Processed/denoise tier — derived from raw by `gps-processor`, fully rebuildable (see `.claude/modules/processor.md`):

- `track_points(...)` — the denoised/simplified points the frontend reads via `/api/points` (`kind` `track`|`stop`, `n_raw`, `importance`, `accuracy`, stop `dwell_*`/`radius`, `src_raw_id`). Phase 3 collapses each parked dwell to one accuracy-weighted point and simplifies moving segments (Reumann–Witkam, `importance` = perpendicular deviation).
- `track_events(...)` — processor-emitted events (stop start/end, mode transitions, …); distinct from the user-curated `annotations`.
- `receiver_metadata(id, timestamp, hdop, vdop, pdop, nsat_used, nsat_seen)` — SKY-sourced DOP + sat counts, written by the logger on a ~5 s throttle; standalone telemetry, not joined into the position path.
- `processing_state(key, value)` — the processor's `last_committed_raw_id` cursor.

The same DB also holds the sensor-platform tables (`sensors`, `bme680_readings`, `alarm_rules`, `alarm_events`) — see the Sensor Platform section below.

### API Endpoints

- `GET /api/points?start=&end=&limit=&bbox=` — trail/history for a time range, read from the processed tier (`track_points`), size-aware decimated (C17): every `kind='stop'` whose dwell interval overlaps the window is kept, then the remaining `limit` budget (default 5000, max 20000) is filled with the highest-`importance` moving vertices and the result re-sorted by time. `truncated` ⇒ moving vertices were dropped (stops never are). Optional `bbox=W,S,E,N`. Each point carries `kind`/`n_raw`/`importance`/`accuracy`.
- `GET /api/points/latest` — single most-recent **raw** fix (the live position dot reads raw `gps_points`, not the processed tier, so it tracks the true current fix — C13)
- `GET /api/annotations` — list every annotation; `point_count` is NULL for point bookmarks, integer for ranges
- `POST /api/annotations` — create annotation; omit (or pass null) `end_time` for a point bookmark
- `PATCH /api/annotations/:id` — edit name, notes, or bounds (including transitioning point↔range)
- `DELETE /api/annotations/:id`
- `GET/POST /api/annotations/mark` — read the persisted `start`/`end` marks (restores live range-construction across reloads); POST upserts one with current UTC time
- `GET /tiles/osm.pmtiles` — vector OSM basemap (single PMTiles archive) served with HTTP range support
- `GET /tiles/terrain.pmtiles` — terrain DEM (Mapzen Terrarium PNGs in a PMTiles archive) served with HTTP range support; read client-side by MapLibre via `raster-dem` + `encoding: 'terrarium'`
- `GET /tiles/<layer>/{z}/{x}/{y}.png` — raster tile proxy/cache (USGS); `?refresh=1` serves from cache and fires a background ETag-conditional GET, updating the cache if the tile changed
- `GET /api/sensors` — sensor registry, each row with its latest reading embedded
- `GET /api/sensors/:id/readings?start=&end=&limit=` — reading history for the trend chart (defaults to the trailing 24h)
- `GET /api/gpsd/sky` — live satellite constellation straight from gpsd's SKY + TPV (no DB/schema): per-sat az/el/SNR/used/constellation, the full DOP set (h/v/p/x/y/g/t), used/seen counts, plus heading (`track`) and `speed`; feeds the skyplot
- `GET /gpsd` — read-only gpsd status page
- `GET /skyplot` — live 3D satellite skyplot page
- `GET /ntp` — read-only NTP/chrony status page
- `GET /sensors` — sensor viewer (current values + trend charts)

### Frontend

Plain files in `static/` + `templates/`, all JS/CSS vendored in `static/vendor/` (no CDN at runtime), mobile-first. One map-centric MapLibre view at `/` (time picker, sub-range slider, annotations drawer, server-side size-aware decimation) plus four standalone pages — `/gpsd`, `/skyplot`, `/ntp`, `/sensors`. See **`.claude/modules/frontend.md`** for the view controls and per-page detail.

### Basemaps & Terrain

A single MapLibre map (`MapView`, `static/js/map.js`) renders two basemaps plus a terrain DEM: **vector OSM** (default — an immutable `northamerica.pmtiles` served at `/tiles/osm.pmtiles`, rendered client-side), **raster USGS** (online proxy + offline disk cache at `/tiles/<layer>/{z}/{x}/{y}.png`), and a **Terrarium terrain DEM** (`/tiles/terrain.pmtiles`) MapLibre drapes the basemap on for 3D. The ⚙ Labels and 🏔 3D panels drive the map directly. See **`.claude/modules/basemaps.md`** for archive paths/env, tile route + cache mechanics, draping, and the precache/terrain-build tooling.

### GPS Logger Detail

Bypasses the Python `gps` library in favor of a direct TCP socket to gpsd on `localhost:2947`. Sends `?WATCH={"enable":true,"json":true}\n`, parses TPV JSON records. Motion-gates raw writes: the full nav rate (~5 Hz) while moving, throttled to ~1 Hz while parked (Doppler speed < 0.5 m/s) — parked 5 Hz is correlated bloat the processor's static-hold collapses anyway. SKY-sourced DOP + sat counts write to `receiver_metadata` on a separate ~5 s throttle. Reconnects automatically on failure with 5s backoff.

Two layers of stall detection: a 30s socket timeout catches a fully frozen gpsd (no bytes at all), and a staleness watchdog forces a reconnect if no valid fix is seen for 120s *while data is still flowing* — the case the socket timeout misses, since gpsd keeps emitting SKY/no-fix TPV. Every 60s it logs a heartbeat with points written, current fix mode, age of the last write, and a breakdown of dropped records by reason (no_fix, no_latlon, bad_range, null_island, stale_time, throttled, json_err), so a silent stall names its own cause in the journal.

### Sensor Platform (MQTT)

A second data stream beyond GPS: environmental sensors ingested over a local mosquitto MQTT bus into the **same** SQLite DB, for GPS↔sensor correlation. Broker + ingest + the first remote node are live — a BME680 on an ESPHome ESP32-C6 (`firmware/cabin-bme680.yaml`) running Bosch BSEC2 for a calibrated IAQ index, publishing to `sensors/cabin/bme680`. The `/sensors` page (current values + trend charts) reads the ingested data straight from the DB — see the Frontend section. *Live* (push) browser readouts via MQTT-over-WS and alarms are still planned (the WS transport is blocked on the broker; the DB-backed viewer sidesteps it). GPS logging is untouched and stays off the bus. See **`.claude/modules/sensors.md`** for the architecture and remaining roadmap.

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
│   ├── cabin-bme680.yaml       # XIAO ESP32-C6 + BME680 (BSEC2 IAQ)
│   ├── README.md
│   └── secrets.yaml.example    # copy to secrets.yaml before flashing
├── static/
│   ├── css/app.css
│   ├── img/tile-error.png
│   ├── js/
│   │   ├── api.js, app.js, geo.js, map.js, labels.js, timeline.js, annotations.js
│   │   ├── sensors.js      # /sensors viewer (current values + uPlot charts)
│   │   └── skyplot.js      # /skyplot 3D satellite hemisphere (plain canvas)
│   └── vendor/
│       ├── nouislider/
│       ├── maplibre/       # maplibre-gl
│       ├── pmtiles/        # pmtiles.js range reader
│       ├── uplot/          # uPlot time-series charts (sensor trends)
│       └── basemap/        # Protomaps style.json + glyphs + sprite
├── templates/
│   ├── index.html
│   ├── gpsd.html
│   ├── skyplot.html
│   ├── ntp.html
│   └── sensors.html
├── tools/
│   ├── precache.py
│   ├── fetch_terrain_tiles.py  # Mapzen Terrarium → MBTiles (asyncio+httpx)
│   ├── regions.py              # shared Region dataclass + REGIONS table
│   ├── gpsd_setup.py
│   ├── gpsd_validate.py
│   ├── ntp_setup.py
│   ├── ntp_validate.py
│   └── obd_probe.py            # OBD-II Phase-0 connectivity probe (plans/obd-platform-plan.md)
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
├── plans/                      # active/in-flight plans (landed ones fold into .claude/modules/)
└── pyproject.toml
```

## Hardware Notes

Current GPS: a u-blox **NEO-M9N** read by gpsd as **UBX binary** on `/dev/ttyAMA0` @ 38400, 5 Hz, 4-constellation; PPS on GPIO 4 → `/dev/pps0` drives chrony stratum 1. **Baud trap:** keep gpsd at the module's *reset-default* baud (38400), never a higher forced rate — a power-drained config revert once desynced gpsd and stalled the logger silently for days. Full module/PPS/baud detail, legacy hardware, and the gpsd/NTP setup + validation tooling are in **`.claude/modules/hardware.md`**.

## Tool Scripts

All scripts in `tools/` must handle `KeyboardInterrupt` gracefully — print `"\nInterrupted."` and exit with code 130. Never let Ctrl+C produce a traceback. For scripts using `ThreadPoolExecutor`, catch `KeyboardInterrupt` inside the `as_completed` loop, cancel pending futures, print partial stats, and exit 130.

## Commands

```bash
# Install dependencies
uv sync

# Run the web app locally (module form — api/app.py uses package imports)
uv run python -m api.app

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
