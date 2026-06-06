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

The hook runs `uv sync`, always restarts `gps-dashboard`, and restarts `gps-logger` only if `logger/` changed (to avoid GPS data gaps). The `pi` remote points to `pmorgan@192.168.42.178:/mnt/nvme/gps-dashboard.git`.

App files live on an NVMe drive mounted at `/mnt/nvme`:
- `/mnt/nvme/gps-dashboard.git` — bare repo (deploy target)
- `/mnt/nvme/gps-dashboard` — working tree (overwritten by deploys)
- `/mnt/nvme/data/gps_history.db` — database (persists across deploys)
- `/mnt/nvme/cache/tiles/` — tile cache (persists across deploys)

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

- **Logger** (`logger/gps_logger.py`) — standalone script, no Flask. Reads from gpsd via TCP socket on `localhost:2947`, writes to SQLite. The only process that writes GPS points.
- **Web app** (`api/app.py`) — Flask, read-heavy. Serves the frontend, JSON API, tile proxy, and status pages.

### Data Model

SQLite (`gps_history.db`), three tables:

- `gps_points(id, timestamp, lat, lon, speed, altitude, track)` — continuous append-only stream
- `trips(id, name, start_time, end_time, notes)` — pure metadata; no foreign keys. Points for a trip queried via `WHERE timestamp BETWEEN start_time AND end_time`.
- `marks(key, timestamp)` — two rows max (`start`, `end`); persists live trip-marking timestamps across restarts.

### API Endpoints

- `GET /api/points?start=&end=&limit=` — points for a time range (default limit 5000, max 20000)
- `GET /api/points/latest` — single most-recent point
- `GET /api/trips` — list all trips with `point_count`
- `POST /api/trips` — create trip
- `PATCH /api/trips/:id` — edit name, notes, or bounds
- `DELETE /api/trips/:id`
- `POST /api/trips/mark` — upsert `start` or `end` mark with current UTC time
- `GET /tiles/{z}/{x}/{y}.png` — tile proxy; `?refresh=1` serves from cache and fires a background conditional GET against OSM (ETag-based), updating the cache if the tile changed
- `GET /gpsd` — read-only gpsd status page
- `GET /ntp` — read-only NTP/chrony status page

### Frontend

Separate files in `static/` and `templates/`. All JS/CSS vendored in `static/vendor/` — no CDN calls at runtime. Mobile-first (primary client is a phone browser).

Two views in the main app (`/`):
- **Timeline** — date picker + range scrubber (noUiSlider), filters points in memory, create trips from selection. **Live mode**: when viewing today's date, the Live button polls `/api/points` every 30s and auto-advances the slider end to show new points in real time. The ⊕ FAB button zooms the map to the most recent GPS fix via `/api/points/latest`.
- **Trips** — browse trips, view track on map, stats (distance, max/avg speed, elevation gain) computed client-side via Haversine

Two standalone status pages:
- `/gpsd` — gpsd service state, fix mode, satellite count, latest coordinates, pass/fail indicators
- `/ntp` — chrony sync status, stratum, offset, GPS/PPS source state, LAN server status

Both status pages auto-refresh every 30 seconds.

### Tile Proxy & Cache

Flask proxies tile requests to upstream tile servers when online, caches to disk at `$GPS_TILE_CACHE_DIR/<layer>/{z}/{x}/{y}.png` (set to `/mnt/nvme/cache/tiles` on the Pi; falls back to `~/.cache/gps-dashboard/tiles/` in dev). Serves from cache offline. Returns 503 if tile is uncached and internet is unavailable.

Supported layers are defined in `api/tile_layers.py` (single source of truth, imported by both the route and the precache tool):
- `osm` — OpenStreetMap, max zoom 19
- `usgs` — USGS Topo via the National Map basemap service, max zoom 16. Public domain, no API key, bulk-download permitted.

Route shape: `GET /tiles/<layer>/<z>/<x>/<y>.png`. Unknown layer or z past the layer's max → 400.

ETags are stored in sidecar files (`<layer>/{z}/{x}/{y}.etag`) alongside each cached tile. The `?refresh=1` query param triggers a background conditional GET (`If-None-Match`) per tile; the cache is updated silently if the upstream returns a new version. A "↻" checkbox in the UI tab bar enables refresh mode for both maps; a layer dropdown next to it switches the active layer globally. Tile writes are atomic (write to thread-unique `.tmp`, then `replace`), so a crash mid-write can never leave a torn PNG.

`tools/precache.py` pre-downloads tiles for a bounding box + zoom range, for a single layer at a time (`--layer osm` default, or `--layer usgs`). Includes a state bounding box lookup table. Practical zoom range: z8–z15 for OSM, z8–z14 for USGS. Supports three source modes: `--region`, `--bbox`, and `--local` (derives bbox from current GPS position in the database with a configurable `--radius` in km, default 50).

### GPS Logger Detail

Bypasses the Python `gps` library in favor of a direct TCP socket to gpsd on `localhost:2947`. Sends `?WATCH={"enable":true,"json":true}\n`, parses TPV JSON records. Uses a 30s socket timeout so it detects a frozen gpsd. Throttles DB writes to one point per 5s. Reconnects automatically on failure with 5s backoff.

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

### Project Structure

```
gps-dashboard/
├── api/
│   ├── app.py
│   ├── db.py
│   ├── tile_layers.py
│   └── routes/
│       ├── points.py
│       ├── trips.py
│       ├── tiles.py
│       ├── status_gpsd.py
│       └── status_ntp.py
├── logger/
│   └── gps_logger.py
├── static/
│   ├── css/app.css
│   ├── img/tile-error.png
│   ├── js/
│   │   ├── api.js, app.js, geo.js, map.js, timeline.js, trips.js
│   └── vendor/
│       ├── leaflet/
│       └── nouislider/
├── templates/
│   ├── index.html
│   ├── gpsd.html
│   └── ntp.html
├── tools/
│   ├── precache.py
│   ├── gpsd_setup.py
│   ├── gpsd_validate.py
│   ├── ntp_setup.py
│   └── ntp_validate.py
├── deploy/
│   ├── gps-dashboard.service
│   ├── gps-logger.service
│   ├── chrony-gps-only.conf
│   ├── chrony-gps-pps.conf
│   └── 99-gps-dongle.rules
├── docs/
│   └── plan.md
└── pyproject.toml
```

## Hardware Notes

Current GPS hardware: a serial GPS module with PPS, wired to the Raspberry Pi (CM5) GPIO header. NMEA arrives on the primary header UART `/dev/ttyAMA0` at 9600 baud; PPS is on GPIO 4 (`pps-gpio` overlay → `/dev/pps0`). gpsd and the logger both reference `/dev/ttyAMA0`. NTP runs in GPS+PPS mode (chrony stratum 1, sub-microsecond accuracy via PPS).

The module runs at 9600 — its factory default — set via `GPSD_OPTIONS="-n -s 9600"` in `/etc/default/gpsd`. This is deliberate: an earlier attempt to drive it at 115200 was lost when the module's config-backup power drained (cable borrowed mid-trip), reverting it to 9600 on the next reboot while gpsd kept forcing 115200 — gpsd then silently received nothing and the logger stalled invisibly for days. Keeping both at 9600 means a power loss can't desync the module from gpsd. PPS, not baud, drives timing precision, so 9600 costs nothing.

Legacy: the previous hardware was a u-blox 7 USB dongle (VID 1546, PID 01a7) pinned to `/dev/gps0` via `deploy/99-gps-dongle.rules` and run GPS-only (stratum 10, ~100ms). That udev rule and the `/dev/gps0` path apply only to the USB dongle, not the current serial GPS.

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

# gpsd setup and validation (run on Pi)
uv run tools/gpsd_setup.py
uv run tools/gpsd_validate.py

# NTP setup and validation (run on Pi)
uv run tools/ntp_setup.py
uv run tools/ntp_validate.py

# Inspect the database
sqlite3 "$GPS_DB_PATH" "SELECT * FROM gps_points ORDER BY id DESC LIMIT 10;"
sqlite3 "$GPS_DB_PATH" "SELECT * FROM trips;"
```

No test suite or linter is configured.

## Offline Constraint

All runtime dependencies must work without internet. When adding new frontend libraries, vendor them into `static/vendor/`. Python packages install from `uv.lock` at deploy time — no network needed after `uv sync`. The tile proxy handles map assets at runtime.

Development happens with internet available. Pre-caching tiles and vendoring assets are intentional prep steps before going off-grid.
