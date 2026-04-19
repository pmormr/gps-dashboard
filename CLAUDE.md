# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A GPS history browser for a Raspberry Pi installed in a van, serving a local network (LAN) that is frequently off-grid with no internet access. The Pi logs GPS data continuously; this app is the interface for reviewing, tagging, and analyzing that data.

Users connect via phone or laptop over the van's WiFi. No authentication is required — the LAN is trusted.

The full implementation plan is at `docs/plan.md`.

## Deployment

Two systemd services run on the Pi: `gps-logger` (writes GPS data) and `gps-dashboard` (serves the web app). Both are managed via a bare git repo with a post-receive hook.

```bash
# Deploy to Pi
git push pi main
```

The hook runs `uv sync`, always restarts `gps-dashboard`, and restarts `gps-logger` only if `logger/` changed (to avoid GPS data gaps). The `pi` remote points to `pmorgan@192.168.42.178:/home/pmorgan/gps-dashboard.git`.

```bash
# Add the remote if it's missing
git remote add pi pmorgan@192.168.42.178:/home/pmorgan/gps-dashboard.git

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
- `GET /tiles/{z}/{x}/{y}.png` — tile proxy
- `GET /gpsd` — read-only gpsd status page
- `GET /ntp` — read-only NTP/chrony status page

### Frontend

Separate files in `static/` and `templates/`. All JS/CSS vendored in `static/vendor/` — no CDN calls at runtime. Mobile-first (primary client is a phone browser).

Two views in the main app (`/`):
- **Timeline** — date picker + range scrubber (noUiSlider), filters points in memory, create trips from selection
- **Trips** — browse trips, view track on map, stats (distance, max/avg speed, elevation gain) computed client-side via Haversine

Two standalone status pages:
- `/gpsd` — gpsd service state, fix mode, satellite count, latest coordinates, pass/fail indicators
- `/ntp` — chrony sync status, stratum, offset, GPS/PPS source state, LAN server status

Both status pages auto-refresh every 30 seconds.

### Tile Proxy & Cache

Flask proxies tile requests to OpenStreetMap when online, caches to disk at `~/.cache/gps-dashboard/tiles/{z}/{x}/{y}.png`. Serves from cache offline. Returns 503 if tile is uncached and internet is unavailable.

`tools/precache.py` pre-downloads tiles for a bounding box + zoom range. Includes a state bounding box lookup table. Practical zoom range: z8–z15; z16+ grows storage quickly.

### GPS Logger Detail

Bypasses the Python `gps` library in favor of a direct TCP socket to gpsd on `localhost:2947`. Sends `?WATCH={"enable":true,"json":true}\n`, parses TPV JSON records. Uses a 30s socket timeout so it detects a frozen gpsd. Throttles DB writes to one point per 5s. Reconnects automatically on failure with 5s backoff.

### gpsd & NTP Setup

Setup and validation are handled by CLI scripts in `tools/`, not through the web UI. Service config templates live in `deploy/`.

- `tools/gpsd_setup.py` — interactive: detects devices, writes `/etc/default/gpsd`, restarts gpsd
- `tools/gpsd_validate.py` — checks service, device, fix, data flow; prints PASS/FAIL per check
- `tools/ntp_setup.py` — interactive: configures chrony with GPS SHM source, optional PPS; enables Pi as LAN NTP server
- `tools/ntp_validate.py` — checks chrony sync, GPS/PPS source, stratum, LAN serving

Two chrony config templates:
- `deploy/chrony-gps-only.conf` — USB dongle, no PPS (~100ms accuracy), stratum 10
- `deploy/chrony-gps-pps.conf` — serial dongle with PPS (microsecond accuracy), stratum 1

### Project Structure

```
gps-dashboard/
├── api/
│   ├── app.py
│   ├── db.py
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
│   └── chrony-gps-pps.conf
├── docs/
│   └── plan.md
└── pyproject.toml
```

## Commands

```bash
# Install dependencies
uv sync

# Run the web app locally
uv run api/app.py

# Pre-cache tiles for a region
uv run tools/precache.py --region colorado --zoom 8-15
uv run tools/precache.py --bbox "-109.05,36.99,-102.04,41.00" --zoom 8-15
uv run tools/precache.py --list-regions

# gpsd setup and validation (run on Pi)
uv run tools/gpsd_setup.py
uv run tools/gpsd_validate.py

# NTP setup and validation (run on Pi)
uv run tools/ntp_setup.py
uv run tools/ntp_validate.py

# Inspect the database
sqlite3 gps_history.db "SELECT * FROM gps_points ORDER BY id DESC LIMIT 10;"
sqlite3 gps_history.db "SELECT * FROM trips;"
```

No test suite or linter is configured.

## Offline Constraint

All runtime dependencies must work without internet. When adding new frontend libraries, vendor them into `static/vendor/`. Python packages install from `uv.lock` at deploy time — no network needed after `uv sync`. The tile proxy handles map assets at runtime.

Development happens with internet available. Pre-caching tiles and vendoring assets are intentional prep steps before going off-grid.
