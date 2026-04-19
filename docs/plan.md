# GPS Dashboard — Full Rewrite Plan

## Context

The current codebase is a single-file proof of concept (`main.py`) with a flat GPS log, embedded HTML frontend, and no trip concept. We're doing a ground-up rewrite to a proper multi-file architecture with a continuous GPS point stream, trip metadata, offline-first tile proxy, and a mobile-first history browser UI.

Key constraints:
- Runs on a Raspberry Pi on a local LAN, frequently off-grid (no internet)
- All JS vendored locally — no CDN calls at runtime
- Two systemd services: logger (writes DB) and web app (reads DB)
- Deploy via `git push pi main`

---

## Decisions Baked In

| # | Decision | Choice |
|---|----------|--------|
| 1 | Old `location_history` migration | Drop it (user gave permission to delete existing data) |
| 2 | Points API large ranges | `limit` param (default 5000, max 20000) |
| 3 | Trip mark persistence | SQLite `marks` table (survives restarts) |
| 4 | Logger restart on deploy | Only when `logger/gps_logger.py` changed |
| 5 | Timeline date picker | Native `<input type="datetime-local">` |

**Future work noted:** A GPS data compression tool (remove/average points where vehicle isn't moving) — defer to a later phase.

---

## Target Project Structure

```
gps-dashboard/
├── api/
│   ├── __init__.py
│   ├── app.py
│   ├── db.py
│   └── routes/
│       ├── __init__.py
│       ├── points.py
│       ├── trips.py
│       └── tiles.py
├── logger/
│   ├── __init__.py
│   └── gps_logger.py
├── static/
│   ├── css/app.css
│   ├── img/tile-error.png
│   ├── js/
│   │   ├── api.js
│   │   ├── app.js
│   │   ├── geo.js
│   │   ├── map.js
│   │   ├── timeline.js
│   │   └── trips.js
│   └── vendor/
│       ├── leaflet/        (leaflet.js, leaflet.css, images/)
│       └── nouislider/     (nouislider.min.js, nouislider.min.css)
├── templates/
│   └── index.html
├── tools/
│   └── precache.py
├── deploy/
│   ├── gps-dashboard.service
│   └── gps-logger.service
├── main.py                 (DELETE after Phase 6 confirmed working)
└── pyproject.toml
```

---

## Phase 1: Data Layer

**Files:** `api/db.py`, `api/__init__.py`, `api/routes/__init__.py`, `pyproject.toml`

### `api/db.py`
- `DB_PATH` — reads from env var `GPS_DB_PATH`, defaults to `~/gps_history.db`
- `get_connection()` — returns `sqlite3.connect(DB_PATH, check_same_thread=False)` with `row_factory = sqlite3.Row`
- `init_db(conn)` — creates all tables with `CREATE TABLE IF NOT EXISTS`:

```sql
CREATE TABLE IF NOT EXISTS gps_points (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    lat       REAL NOT NULL,
    lon       REAL NOT NULL,
    speed     REAL,
    altitude  REAL,
    track     REAL
);
CREATE INDEX IF NOT EXISTS idx_gps_points_timestamp ON gps_points(timestamp);

CREATE TABLE IF NOT EXISTS trips (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time   TEXT NOT NULL,
    notes      TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_trips_start_time ON trips(start_time);

CREATE TABLE IF NOT EXISTS marks (
    key       TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL
);
```

- `migrate(conn)` — checks for `location_history` table; if present, drops it and prints row count. No data copy needed (user approved deletion).

### `pyproject.toml` changes
- Remove `gps>=3.19` (unused — we use raw sockets)
- Add `requests>=2.32` (tile proxy)
- Add `click>=8.0` (precache CLI)

### Verification
```bash
python -c "from api.db import get_connection, init_db, migrate; conn = get_connection(); init_db(conn); migrate(conn); print('OK')"
sqlite3 ~/gps_history.db ".tables"
# Expected: gps_points  marks  trips
```

---

## Phase 2: GPS Logger

**Files:** `logger/__init__.py`, `logger/gps_logger.py`

Standalone script — no Flask, no threads. Runs as its own systemd service.

### Key implementation details

**Socket protocol** (reuse logic from existing `main.py`):
```python
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(30)   # timeout prevents hanging if gpsd freezes
sock.connect(('127.0.0.1', 2947))
sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
f = sock.makefile('r', encoding='utf-8', errors='replace')
```

**Timestamp:** Use `datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')` — wall clock, not gpsd's `time` field (which can be absent or stale).

**NULL handling:** Use `report.get('speed')` etc. — store `None` as SQL NULL. Do not default absent fields to `0.0`.

**Throttling:** `LOG_INTERVAL_SECONDS = 5`. Track `last_log_time` with `time.monotonic()`. Reset per connection session (not per process start).

**Retry loop:**
```python
while True:
    try:
        run_session(conn)
    except KeyboardInterrupt:
        break
    except Exception as e:
        print(f"GPS error: {e}, reconnecting in 5s", file=sys.stderr)
        time.sleep(5)
```

**DB connection:** Open once at process start, pass to `run_session()`. Logger is single-threaded.

### Verification
```bash
uv run logger/gps_logger.py &
watch -n 5 'sqlite3 ~/gps_history.db "SELECT COUNT(*) FROM gps_points;"'
```

---

## Phase 3: API Core

**Files:** `api/app.py`, `api/routes/points.py`, `api/routes/trips.py`, `api/routes/tiles.py` (stub)

### `api/app.py`
Application factory pattern:
```python
def create_app():
    app = Flask(__name__, static_folder='../static', template_folder='../templates')
    app.register_blueprint(points_bp)
    app.register_blueprint(trips_bp)
    app.register_blueprint(tiles_bp)
    return app

if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=5000, debug=False)
```

Call `init_db()` and `migrate()` at startup.

### `api/routes/points.py`

- `GET /api/points?start=<iso8601>&end=<iso8601>&limit=<int>` — required: `start`, `end`. Optional: `limit` (default 5000, max 20000). Returns `{"points": [...], "count": N}`.
- `GET /api/points/latest` — single most-recent point or 404.

Query:
```sql
SELECT id, timestamp, lat, lon, speed, altitude, track
FROM gps_points
WHERE timestamp >= :start AND timestamp <= :end
ORDER BY timestamp ASC
LIMIT :limit
```

Validate timestamps with `datetime.fromisoformat()`, return 400 on bad input.

### `api/routes/trips.py`

- `GET /api/trips` — all trips ordered `start_time DESC`, each with a `point_count` subquery
- `POST /api/trips` — body: `{name, start_time, end_time, notes?}` → 201
- `PATCH /api/trips/<id>` — partial update of any subset of fields → 200
- `DELETE /api/trips/<id>` → 204
- `POST /api/trips/mark` — body: `{marker: "start"|"end"}` — upserts into `marks` table with current UTC time. Returns both marks if both are set: `{"start": "...", "end": "..."}`.

`point_count` subquery:
```sql
SELECT t.*, (
    SELECT COUNT(*) FROM gps_points p
    WHERE p.timestamp >= t.start_time AND p.timestamp <= t.end_time
) as point_count
FROM trips t ORDER BY t.start_time DESC
```

### `api/routes/tiles.py` (stub)
Return 503 for now. Will be replaced in Phase 4.

### Verification
```bash
uv run api/app.py &
curl "http://localhost:5000/api/points/latest"
curl "http://localhost:5000/api/points?start=2020-01-01T00:00:00Z&end=2030-01-01T00:00:00Z"
curl -X POST http://localhost:5000/api/trips \
  -H "Content-Type: application/json" \
  -d '{"name":"Test","start_time":"2025-01-01T00:00:00Z","end_time":"2025-01-02T00:00:00Z"}'
curl http://localhost:5000/api/trips
curl -X POST http://localhost:5000/api/trips/mark -H "Content-Type: application/json" -d '{"marker":"start"}'
curl -X POST http://localhost:5000/api/trips/mark -H "Content-Type: application/json" -d '{"marker":"end"}'
```

---

## Phase 4: Tile Proxy + Pre-cache CLI

**Files:** `api/routes/tiles.py` (full), `tools/precache.py`

### `api/routes/tiles.py`

Cache directory: `TILE_CACHE_DIR` from env var `GPS_TILE_CACHE_DIR`, default `~/.cache/gps-dashboard/tiles`.

Structure on disk mirrors URL: `{cache_dir}/{z}/{x}/{y}.png`.

```python
@tiles_bp.route('/tiles/<int:z>/<int:x>/<int:y>.png')
def tile(z, x, y):
    if not (0 <= z <= 19 and 0 <= x < 2**z and 0 <= y < 2**z):
        abort(400)
    cache_path = TILE_CACHE_DIR / str(z) / str(x) / f"{y}.png"
    if cache_path.exists():
        return send_file(cache_path, mimetype='image/png')
    try:
        resp = requests.get(
            f"https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            timeout=5,
            headers={'User-Agent': 'gps-dashboard/1.0 (pmormr@gmail.com)'}
        )
        resp.raise_for_status()
    except Exception:
        abort(503)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(resp.content)
    return send_file(cache_path, mimetype='image/png')
```

Note: OSM tile usage policy requires a descriptive User-Agent. Include email or repo URL.

### `tools/precache.py`

**Tile math:**
```python
def lat_lon_to_tile(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    lat_r = math.radians(lat)
    y = int((1 - math.asinh(math.tan(lat_r)) / math.pi) / 2 * n)
    return x, y
```

Note: y increases southward. For a bbox `(min_lon, min_lat, max_lon, max_lat)`:
- `x_min, y_max = lat_lon_to_tile(min_lat, min_lon, z)`
- `x_max, y_min = lat_lon_to_tile(max_lat, max_lon, z)`

**State bounding boxes** — dict of `(min_lon, min_lat, max_lon, max_lat)` for at minimum: colorado, utah, arizona, new_mexico, nevada, california, oregon, washington, idaho, montana, wyoming.

**CLI interface (Click):**
```
uv run tools/precache.py --region colorado --zoom 8-14
uv run tools/precache.py --bbox "-109.05,36.99,-102.04,41.00" --zoom 12-15
uv run tools/precache.py --list-regions
```

Always print estimated tile count and ask for confirmation before downloading. Rate-limit to 50ms between requests. Use `ThreadPoolExecutor(max_workers=4)`. Print progress as `Downloaded 1234/45678 (z=12)...`. Skip tiles already in cache.

### Verification
```bash
# Fetch one tile (needs internet):
curl -o /tmp/t.png http://localhost:5000/tiles/10/164/395.png
file /tmp/t.png  # PNG image data
ls ~/.cache/gps-dashboard/tiles/10/164/395.png  # cached

# Pre-cache a tiny area:
uv run tools/precache.py --region colorado --zoom 8-9

# Simulate offline (remove internet or test with a cached tile only):
curl -o /tmp/t2.png http://localhost:5000/tiles/10/164/395.png  # serves from cache
```

---

## Phase 5: Frontend

**Pre-work:** Download and commit vendor assets before writing JS.
- `static/vendor/leaflet/` — leaflet 1.9.4 (leaflet.js, leaflet.css, images/)
- `static/vendor/nouislider/` — noUiSlider 15.8.1 (nouislider.min.js, nouislider.min.css)
- `static/img/tile-error.png` — 256×256 gray placeholder PNG for offline tile failures

### `templates/index.html`
Single HTML file. Two views toggled by CSS. Tab bar fixed at bottom (mobile) / top (desktop). All assets local — no CDN.

```html
<link rel="stylesheet" href="/static/vendor/leaflet/leaflet.css">
<link rel="stylesheet" href="/static/vendor/nouislider/nouislider.min.css">
<link rel="stylesheet" href="/static/css/app.css">
...
<nav id="tab-bar">
  <button data-view="timeline" class="active">Timeline</button>
  <button data-view="trips">Trips</button>
</nav>
<div id="view-timeline" class="view active">...</div>
<div id="view-trips" class="view">...</div>
...
<script src="/static/vendor/leaflet/leaflet.js"></script>
<script src="/static/vendor/nouislider/nouislider.min.js"></script>
<script src="/static/js/api.js"></script>
<script src="/static/js/geo.js"></script>
<script src="/static/js/map.js"></script>
<script src="/static/js/timeline.js"></script>
<script src="/static/js/trips.js"></script>
<script src="/static/js/app.js"></script>
```

### `static/css/app.css`
- CSS Grid layout: tab bar + map fills `100dvh` (use `dvh` not `vh` — mobile browser chrome handling)
- Tab bar at bottom on mobile (`@media (max-width: 767px)`), top on desktop
- Minimum 44px tap targets on all interactive elements

### `static/js/api.js`
Thin fetch wrapper exposing: `getPoints(start, end, limit)`, `getPointsLatest()`, `getTrips()`, `createTrip(data)`, `updateTrip(id, data)`, `deleteTrip(id)`, `markTimestamp(marker)`. All return parsed JSON or throw on non-2xx.

### `static/js/geo.js`
Haversine distance function:
```javascript
function haversineMeters(lat1, lon1, lat2, lon2) { ... }
```
Used by trips stats computation (client-side).

### `static/js/map.js`
Leaflet singleton. Tile URL: `/tiles/{z}/{x}/{y}.png`. `errorTileUrl: '/static/img/tile-error.png'`. Exposes: `init(elementId)`, `showTrack(points)`, `clearTrack()`, `fitToTrack()`.

### `static/js/timeline.js`
- Native `<input type="datetime-local">` for date selection
- On date change: fetch full day's points via `API.getPoints()`, initialize noUiSlider over the day's time range
- On slider change: filter already-fetched points array in memory (no re-fetch), re-render track
- "Create Trip" button: show name/notes form, call `API.createTrip()` with current slider timestamps

### `static/js/trips.js`
- Load and render trips list (name, date range, distance, duration)
- Click trip → fetch its points, show track on map, show stats panel
- Stats computed client-side: total distance (Haversine sum), max/avg speed (exclude NULLs), elevation gain (sum of positive altitude deltas)
- Inline edit for name/notes, delete with confirmation

### `static/js/app.js`
Entry point — initializes map, wires tab switching, initializes Timeline and Trips views.

### Verification
```bash
uv run api/app.py
# On phone browser: http://192.168.42.178:5000
# Check: map renders with local tiles
# Check: timeline scrubber works, track updates without re-fetching
# Check: create trip from timeline selection
# Check: trips list, click trip → track + stats
# Check: mobile layout — tab bar reachable, no overflow
```

---

## Phase 6: Deployment

**Files:** `deploy/gps-logger.service`, `deploy/gps-dashboard.service`

Update post-receive hook on Pi to conditionally restart logger:

```bash
#!/usr/bin/env bash
set -e
REPO_DIR=/home/pmorgan/gps-dashboard
GIT_DIR=/home/pmorgan/gps-dashboard.git
PREV=$(git --git-dir="$GIT_DIR" rev-parse HEAD 2>/dev/null || echo "")
NEW=$(git --git-dir="$GIT_DIR" rev-parse FETCH_HEAD 2>/dev/null || echo "")

git --work-tree="$REPO_DIR" --git-dir="$GIT_DIR" checkout -f main
cd "$REPO_DIR"
/home/pmorgan/.local/bin/uv sync

sudo systemctl restart gps-dashboard

if git --git-dir="$GIT_DIR" diff --name-only "$PREV" "$NEW" 2>/dev/null | grep -q "^logger/"; then
  sudo systemctl restart gps-logger
  echo "Logger restarted (logger/ changed)"
fi

echo "Deploy complete"
```

Update sudoers to also allow `gps-logger` restart:
```
pmorgan ALL=(ALL) NOPASSWD: /bin/systemctl restart gps-dashboard
pmorgan ALL=(ALL) NOPASSWD: /bin/systemctl restart gps-logger
```

Service files use `.venv/bin/python` directly (not `uv run`) for cleaner systemctl output.

Set `GPS_DB_PATH` and `GPS_TILE_CACHE_DIR` env vars in the `gps-dashboard.service` `[Service]` section.

Delete `main.py` once both services confirmed working.

### Verification
```bash
git push pi main
ssh pmorgan@192.168.42.178 "sudo systemctl status gps-logger gps-dashboard --no-pager"
ssh pmorgan@192.168.42.178 "journalctl -u gps-logger -n 10 --no-pager"
curl http://192.168.42.178:5000/api/points/latest
```

---

## Phase 7: gpsd Setup Tooling + Status Page

**Files:** `tools/gpsd_setup.py`, `tools/gpsd_validate.py`, `api/routes/status_gpsd.py`, `templates/gpsd.html`

### `tools/gpsd_setup.py`
Interactive CLI (Click). Guides user through gpsd configuration:
- Auto-detects available devices: scans `/dev/ttyUSB0`, `/dev/ttyUSB1`, `/dev/ttyACM0`, `/dev/ttyACM1`, `/dev/ttyAMA0`, `/dev/ttyS0`
- Prompts to select device and baud rate (4800, 9600, 38400, 115200 — common GPS defaults)
- Writes `/etc/default/gpsd` (requires sudo)
- Restarts gpsd and waits for it to come up
- Runs basic validation (calls `gpsd_validate.py` logic) and reports result

Sample `/etc/default/gpsd` output:
```
START_DAEMON="true"
GPSD_OPTIONS="-n"
DEVICES="/dev/ttyUSB0"
USBAUTO="true"
GPSD_SOCKET="/var/run/gpsd.sock"
```

### `tools/gpsd_validate.py`
Non-interactive validation script. Checks and reports:
1. gpsd service is active (`systemctl is-active gpsd`)
2. Configured device exists in filesystem
3. Port 2947 is accepting connections
4. Data is flowing (opens socket, waits up to 10s for a TPV record)
5. Fix status: fix mode (0=no fix, 2=2D, 3=3D), satellite count
6. Prints PASS/FAIL for each check with brief explanation

### `api/routes/status_gpsd.py`
`GET /gpsd` — renders `templates/gpsd.html` with live status data gathered server-side:
- gpsd service state (`systemctl is-active gpsd`)
- Configured device path (read from `/etc/default/gpsd`)
- Current fix mode and satellite count (query gpsd socket, read one TPV/SKY record)
- Latest point from `gps_points` table (timestamp, lat, lon, speed)
- Pass/fail indicators for: service running, device present, fix acquired, data age < 30s

Register on `api/app.py` blueprint.

### `templates/gpsd.html`
Simple read-only status page. Auto-refreshes every 30 seconds. Mobile-friendly. Shows:
- Large pass/fail status banner at top
- Service status, device, fix mode, satellites
- Latest GPS coordinates and speed
- Last updated time
- Link to run validation script (display only — "run `tools/gpsd_validate.py` for full diagnostics")

### Verification
```bash
# On Pi:
uv run tools/gpsd_setup.py           # interactive setup
uv run tools/gpsd_validate.py        # non-interactive check
curl http://192.168.42.178:5000/gpsd # status page
```

---

## Phase 8: NTP/Chrony Setup Tooling + Status Page

**Files:** `tools/ntp_setup.py`, `tools/ntp_validate.py`, `deploy/chrony-gps-only.conf`, `deploy/chrony-gps-pps.conf`, `api/routes/status_ntp.py`, `templates/ntp.html`

### `tools/ntp_setup.py`
Interactive CLI (Click). Two modes selectable at runtime:

**GPS-only mode** (USB dongle, no PPS):
- Installs chrony if not present (`apt install chrony`)
- Configures chrony with gpsd SHM source (SHM 0, refid GPS, precision 1e-1)
- Configures Pi as NTP server for LAN: `allow 192.168.0.0/16`
- Writes config from `deploy/chrony-gps-only.conf` template to `/etc/chrony/chrony.conf`
- Restarts chrony, waits for GPS source to appear

**GPS+PPS mode** (serial dongle with PPS):
- All of GPS-only steps above, plus:
- Enables UART on Pi (`/boot/firmware/config.txt`: `enable_uart=1`, disable serial console)
- Configures PPS kernel module (`/etc/modules`: add `pps-gpio`)
- Sets GPIO pin for PPS (default GPIO 18, configurable)
- Adds PPS source to chrony config (SHM 1, refid PPS, precision 1e-7, prefer)
- Writes config from `deploy/chrony-gps-pps.conf` template

### `deploy/chrony-gps-only.conf`
```
# GPS SHM source (no PPS — ~100ms accuracy)
refclock SHM 0 refid GPS precision 1e-1 offset 0.0 delay 0.2

# Internet fallback pools (used when online)
pool 2.debian.pool.ntp.org iburst

# Serve time to LAN
allow 192.168.0.0/16
local stratum 10   # advertise even when not synced, so LAN clients don't lose NTP

driftfile /var/lib/chrony/drift
makestep 1.0 3
rtcsync
```

### `deploy/chrony-gps-pps.conf`
```
# GPS SHM source for coarse time
refclock SHM 0 refid GPS precision 1e-1 offset 0.0 delay 0.2 noselect

# PPS source for precise timing (requires GPS to be locked first)
refclock SHM 1 refid PPS precision 1e-7 prefer

# Internet fallback pools
pool 2.debian.pool.ntp.org iburst

# Serve time to LAN
allow 192.168.0.0/16
local stratum 1

driftfile /var/lib/chrony/drift
makestep 1.0 3
rtcsync
```

Note: `local stratum 10` in GPS-only vs `stratum 1` in PPS mode reflects the actual accuracy difference.

### `tools/ntp_validate.py`
Non-interactive validation. Checks and reports:
1. chrony service is active
2. GPS SHM source is visible (`chronyc sources`)
3. chrony is synced (reference not `?`)
4. Current stratum
5. PPS source present and locked (if in PPS mode)
6. Pi is serving NTP to LAN (port 123 listening)
7. Current offset from reference

### `api/routes/status_ntp.py`
`GET /ntp` — renders `templates/ntp.html` with live data from `chronyc tracking` and `chronyc sources -v`:
- Sync status and reference source (GPS / PPS / internet pool)
- Current stratum
- System time offset and RMS offset
- Last update time
- Whether Pi is serving NTP (port 123 open)
- Pass/fail indicators: chrony running, GPS source visible, synced, stratum ≤ 10

### `templates/ntp.html`
Same style as `gpsd.html`. Auto-refreshes every 30 seconds. Shows:
- Large pass/fail banner
- Sync source, stratum, offset
- GPS/PPS lock status
- LAN NTP server status
- "run `tools/ntp_validate.py` for full diagnostics" note

### Verification
```bash
# On Pi:
uv run tools/ntp_setup.py            # interactive setup (choose GPS-only or GPS+PPS)
uv run tools/ntp_validate.py         # non-interactive check
chronyc tracking                     # manual verify
curl http://192.168.42.178:5000/ntp  # status page
```

---

## Updated Project Structure

```
gps-dashboard/
├── api/
│   ├── __init__.py
│   ├── app.py
│   ├── db.py
│   └── routes/
│       ├── __init__.py
│       ├── points.py
│       ├── trips.py
│       ├── tiles.py
│       ├── status_gpsd.py
│       └── status_ntp.py
├── logger/
│   ├── __init__.py
│   └── gps_logger.py
├── static/
│   ├── css/app.css
│   ├── img/tile-error.png
│   ├── js/
│   │   ├── api.js
│   │   ├── app.js
│   │   ├── geo.js
│   │   ├── map.js
│   │   ├── timeline.js
│   │   └── trips.js
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
├── main.py                 (DELETE after Phase 6 confirmed working)
└── pyproject.toml
```

---

## Future Work (Noted, Not Planned)

- **GPS data compression tool** — remove or average points where vehicle isn't moving, to reduce DB size over long periods. Add as a new `tools/compress.py` CLI when needed.
