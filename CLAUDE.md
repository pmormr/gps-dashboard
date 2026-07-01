# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A GPS history browser for a Raspberry Pi installed in a van, serving a local network (LAN) that is frequently off-grid with no internet access. The Pi logs GPS data continuously; this app is the interface for reviewing, tagging, and analyzing that data.

Users connect via phone or laptop over the van's WiFi. No authentication is required — the LAN is trusted.

## Documentation layout

This file is the architectural map and router: base architecture + pointers. Landed subsystem detail lives in `.claude/modules/` (`frontend`, `basemaps`, `hardware`, `processor`, `sensors`, `observatory`, `drone`); **active/in-flight** plans live in `plans/` (`obd-platform`, `motion-imu`, `radio-platform`, `sensor-ideas`). Keep all of it to **current state, critical traps, and eliminated pathways** — the back-and-forth that produced a decision belongs in git history, not here. When a plan lands, fold its durable bits into the relevant module and drop the plan.

`reference/` holds vendored equipment docs (vendor manuals, datasheets) for hardware we may need to consult off-grid — committed rather than gitignored so they ride to the headless Pi. Alongside each PDF, commit a `pdftotext -layout` extraction (same basename, `.txt`) so the doc stays grep-able over SSH without poppler installed on the Pi.

## Deployment

Two systemd services run on the Pi: `gps-logger` (writes GPS data) and `gps-dashboard` (serves the web app). Both are managed via a bare git repo with a post-receive hook.

```bash
# Commit and push to both GitHub and Pi in one step (preferred)
git push all main
```

The hook runs `uv sync` (which also builds the project as an editable install — see Offline Constraint), then restarts services based on what changed. It always restarts `gps-dashboard` and (if enabled) `mqtt-ingest` and `gps-processor`. When any `deploy/` file changed it reinstalls all unit files into `/etc/systemd/system/`, `daemon-reload`s, and enables the `gps-drone-sync.timer` — so editing a service's env var (e.g. `GPS_TERRAIN_PMTILES_PATH`) deploys on push with no manual `systemctl` step. `gps-logger` restarts only if `logger/` (or its unit) changed, to avoid GPS data gaps; `mosquitto`/`sensor-bme680` restart only on their own config/source changes; `gps-drone-sync` is a timer-driven oneshot, not restarted. The `pi` remote points to `pmorgan@192.168.42.178:/mnt/nvme/gps-dashboard.git`.

App files live on an NVMe drive mounted at `/mnt/nvme`:
- `/mnt/nvme/gps-dashboard.git` — bare repo (deploy target)
- `/mnt/nvme/gps-dashboard` — working tree (overwritten by deploys)
- `/mnt/nvme/data/gps_history.db` — database (persists across deploys)
- `/mnt/nvme/cache/tiles/` — raster (USGS) tile cache (persists across deploys)
- `/mnt/nvme/tiles/northamerica.pmtiles` — vector OSM basemap archive, ~33 GB (persists across deploys)
- `/mnt/nvme/tiles/northamerica-terrain.pmtiles` — terrain (Mapzen Terrarium) PMTiles archive, ~105 GB (persists across deploys)
- `/mnt/nvme/paul-network-docs.git` + `/mnt/nvme/paul-network-docs` — the network-docs vault, synced as its **own** bare repo + post-receive checkout (the same pattern as gps-dashboard, but a separate repo). Push from the local `../paul-network-docs` repo with `git push pi main`; the Docs tab reads the checkout via `GPS_NETWORK_DOCS_PATH`. The repo's `.gitignore` keeps secrets/installers out of the sync.

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

Drone telemetry tier — aerial GPS tracks batch-imported from DJI footage by `tools/import_drone.py`, fully rebuildable from the source media (see `.claude/modules/drone.md`):

- `drone_flights(id, model, model_code, first_fix_utc, last_fix_utc, media_path, source_name, n_points, min_lat/min_lon/max_lat/max_lon, imported_at)` — one row per clip. Natural key `(model_code, first_fix_utc)` (no DJI model exposes a serial); `media_path` is the canonical rex-nas path, NULL on an SD-card import for the NAS scan to backfill.
- `drone_track_points(id, flight_id, timestamp, lat, lon, abs_alt, importance)` — the thinned track (Reumann–Witkam, shared via `processor/simplify.py`); canonical ms-UTC puts drone points on the same time axis as `gps_points`. `abs_alt` is MSL metres.

GNSS observatory tier — per-satellite az/el logged for 3D reconstruction + pass prediction; reconstructed/fit on-demand, no rollup (see `.claude/modules/observatory.md`):

- `sat_observations(timestamp, gnssid, svid, az, el, snr, used, health)` — one row per positioned satellite per SKY sweep, on the logger's ~60s throttle; indexed `(gnssid, svid, timestamp)` + `timestamp`. The input the globe reconstructs and pass prediction fits orbits from; standalone telemetry, never joined into the position path.

The same DB also holds the sensor-platform tables (`sensors`, `bme680_readings`, `obd_readings`, `victron_readings`, `alarm_rules`, `alarm_events`) — see the Sensor Platform section below.

### API Endpoints

- `GET /api/status` — Home glance: one aggregate read (latest fix + mode, Victron SOC/solar/load, OBD when the engine was recently on, cabin IAQ/temp, GNSS sat count/fix health, systemd service states); backs the SPA Home view
- `GET /api/points?start=&end=&limit=&bbox=` — trail/history for a time range, read from the processed tier (`track_points`), size-aware decimated (C17): every `kind='stop'` whose dwell interval overlaps the window is kept, then the remaining `limit` budget (default 5000, max 20000) is filled with the highest-`importance` moving vertices and the result re-sorted by time. `truncated` ⇒ moving vertices were dropped (stops never are). Optional `bbox=W,S,E,N`. Each point carries `kind`/`n_raw`/`importance`/`accuracy`; stops also carry `dwell_start`/`dwell_end`/`radius` (the frontend renders them as dwell-interval blocks on the slider and selects them by interval overlap).
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
- `GET /api/obd/economy?start=&end=` — per-window drive summary: speed-density fuel (derived at read time via `common/obd.py`, since `fuel_rate_lph` is stored NULL) integrated over `obd_readings`, divided by the `track_points` path length over the same window, plus `max_speed_kph` and moving/idle/engine-on seconds. Pass an annotation's bounds for per-trip MPG; `calibrated` stays false until a fill-up calibration lands. Backs both the per-range readout (AnnotationsDrawer) and the Map's "inspect this window" panel
- `GET /api/drone/flights?bbox=&start=&end=&points=` — drone flights whose bounds **overlap** the bbox/time filters (all optional), each with its thinned track embedded (`points=0` for metadata only); the map-overlay read
- `POST /api/drone/flights` — idempotent drone-flight ingest (the laptop LAN path via `import_drone.py --api`); body carries identity + thinned points, the server derives time bounds/bbox/`n_points` and dedups on the `(model_code, first_fix_utc)` natural key (201 import / 200 skip|backfill)
- `GET /api/gpsd/sky` — live satellite constellation straight from gpsd's SKY + TPV (no DB/schema): per-sat az/el/SNR/used/constellation, the full DOP set (h/v/p/x/y/g/t), used/seen counts, plus heading (`track`) and `speed`; feeds the skyplot
- `GET /api/gpsd/status` — read-only gpsd device/version/fix snapshot; backs the Systems → gpsd drill-in (`Gpsd.svelte`)
- `GET /api/constellation?start=&end=` — logged `sat_observations` reconstructed to 3D ECEF positions (grouped by SV, with each SV's fitted orbit-plane normal); feeds `/globe`. See `.claude/modules/observatory.md`
- `GET /api/passes?hours=&mask=&track=1` — predicted upcoming satellite passes (orbit fit from logged az/el → propagate): rise/peak/set + az/el, duration, in-progress, sorted by rise. `mask` is the rise/set elevation (`0` = horizon, valid); `track=1` adds a per-pass `[az,el]` polyline for the skyplot overlay
- `GET /api/radio/status` — live ID-5100A main-band state via rigctld (freq/mode/`rawstr` S-meter/tone/repeater/DCD/PTT); `online:false` + service state when rigctld is unreachable (cable unplugged or service disabled)
- `POST /api/radio/freq` · `POST /api/radio/mode` · `POST /api/radio/tone` · `POST /api/radio/repeater` — main-band control writes; 502 on a rig refusal, 503 when rigctld is unreachable
- `GET /api/ntp` — read-only chrony/NTP status (tracking, sources, PPS); backs the Systems → ntp drill-in (`Ntp.svelte`)
- `GET /api/docs/tree` — markdown file tree of the synced `paul-network-docs` vault (`available:false` when `GPS_NETWORK_DOCS_PATH` is unset/missing → the Docs tab shows an empty state)
- `GET /api/docs/file?path=` — raw markdown body of one vault file, realpath-confined to the docs root (traversal-safe, `.md` only); the SPA renders it client-side (markdown-it + lazy mermaid)

**SPA routes** — every non-`api`/`tiles`/`static` path returns the Van OS shell (`dist/index.html`) and renders client-side, *not* a server page: `/` (Home) · `/map` · `/systems` (+ `/trends`, `/gpsd`, `/ntp` drill-ins) · `/docs` (+ `/docs/<vault-path>` deep links) · `/sky` (+ `/globe`, `/skyplot`, `/passes`) · `/radio`. There are no server-rendered pages left — the app is SPA-only.

### Frontend

**Van OS** — a client-side SPA (Svelte 5 + Vite + TypeScript) in `web/`, built to `static/dist/` (committed) and served by Flask (`api/app.py` catch-all → `dist/index.html` for non-`api`/`tiles`/`static` paths). A persistent nav shell with six destinations — **Home** (status glance, `/api/status`) · **Map** (`/map`) · **Systems** (`/systems` + gpsd/ntp drill-ins) · **Docs** (`/docs` — browses the synced `paul-network-docs` vault) · **Sky** (`/sky` = passes + globe/skyplot) · **Radio** (`/radio`). Mobile-first (bottom tabs on phones, sidebar on desktop). Heavy libs (MapLibre, three) are npm deps, **dynamic-imported** so the main bundle stays small; the basemap data assets stay in `static/vendor/basemap/`. **Build + commit `static/dist/` before `git push all`** — the Pi never builds. Charting lives in the SPA's Trends view (`/trends`); the legacy Jinja `/sensors` page + vendored uPlot were retired. See **`.claude/modules/frontend.md`** for shell/router/stores + per-view detail, and **`.claude/modules/observatory.md`** for the globe/passes/skyplot subsystem.

### Basemaps & Terrain

A single MapLibre map (`MapView`, `web/src/lib/map.ts`) renders two basemaps plus a terrain DEM: **vector OSM** (default — an immutable `northamerica.pmtiles` served at `/tiles/osm.pmtiles`, rendered client-side), **raster USGS** (online proxy + offline disk cache at `/tiles/<layer>/{z}/{x}/{y}.png`), and a **Terrarium terrain DEM** (`/tiles/terrain.pmtiles`) MapLibre drapes the basemap on for 3D. The unified **Layers panel** (base map + labels + 3D terrain + drone) drives the map directly. See **`.claude/modules/basemaps.md`** for archive paths/env, tile route + cache mechanics, draping, and the precache/terrain-build tooling.

### GPS Logger Detail

Bypasses the Python `gps` library in favor of a direct TCP socket to gpsd on `localhost:2947`. Sends `?WATCH={"enable":true,"json":true}\n`, parses TPV JSON records. Motion-gates raw writes: the full nav rate (~5 Hz) while moving, throttled to ~1 Hz while parked (Doppler speed < 0.5 m/s) — parked 5 Hz is correlated bloat the processor's static-hold collapses anyway. SKY-sourced DOP + sat counts write to `receiver_metadata` on a separate ~5 s throttle. Reconnects automatically on failure with 5s backoff.

Two layers of stall detection: a 30s socket timeout catches a fully frozen gpsd (no bytes at all), and a staleness watchdog forces a reconnect if no valid fix is seen for 120s *while data is still flowing* — the case the socket timeout misses, since gpsd keeps emitting SKY/no-fix TPV. Every 60s it logs a heartbeat with points written, current fix mode, age of the last write, and a breakdown of dropped records by reason (no_fix, no_latlon, bad_range, null_island, stale_time, throttled, json_err), so a silent stall names its own cause in the journal.

### Sensor Platform (MQTT)

A second data stream beyond GPS: environmental sensors ingested over a local mosquitto MQTT bus into the **same** SQLite DB, for GPS↔sensor correlation. Broker + ingest + the first remote node are live — a BME680 on an ESPHome ESP32-C6 (`firmware/cabin-bme680.yaml`) running Bosch BSEC2 for a calibrated IAQ index, publishing to `sensors/cabin/bme680`. The `/sensors` page (current values + trend charts) reads the ingested data straight from the DB — see the Frontend section. *Live* (push) browser readouts via MQTT-over-WS and alarms are still planned (the WS transport is blocked on the broker; the DB-backed viewer sidesteps it). GPS logging is untouched and stays off the bus. **The van itself is a second stream on this platform** — a Pi-side OBD-II reader (`sensors/obd_reader.py`) publishes `sensors/van/obd` through the same ingest into `obd_readings` (engine RPM/speed/load/temps/fuel, GPS-joinable for per-trip fuel economy); reaching the van's bus needs an FCA Security Gateway bypass harness (`plans/obd-platform-plan.md`). **House power is the third stream** — `sensors/victron_reader.py` bridges the van's **Victron Venus OS GX** (which exposes the whole system over its own keepalive-driven MQTT broker) into `sensors/house/victron` → `victron_readings` (battery / solar / inverter / AC + DC, GPS-joinable for per-trip energy). **The Pi host itself is the fourth stream** — `sensors/system_reader.py` publishes `sensors/pi/system` → `system_readings` (CPU temp/load, memory, root + NVMe disk, uptime, throttle flags) entirely from stdlib `/proc`/`/sys`/`vcgencmd` reads, so the platform reports on its own health. See **`.claude/modules/sensors.md`** for the architecture and remaining roadmap.

### Radio Control (CI-V)

Control the van's **Icom ID-5100A** transceiver from the Pi over its CI-V serial bus. A long-lived **`rigctld`** (Hamlib model **3071**) owns the serial port (cable = OPC-478UC clone / WCH CH343 on a udev-pinned `/dev/icom-civ`, 19200 baud, address 0x8C) and exposes Hamlib's TCP text protocol on `127.0.0.1:4532`; the Flask routes (`api/routes/radio.py`) speak that protocol through a stdlib-socket client (`api/rigctld.py`) — **no** Python Hamlib binding, and the daemon-owns-the-port model solves serial contention. The `/radio` page controls the **active main band only** (the backend can't read which VFO is active): freq/mode/S-meter readout + set, CTCSS/DCS tone, and repeater shift/offset (the backend exposes no memory recall). The `radio-control` service is **enabled-gated** (disabled until the cable is wired). Transmission **recording** (audio plane) and **announcements** (TX, Part-97) are later phases — see **`plans/radio-platform-plan.md`** for the capability map and roadmap.

### Project Structure

```
gps-dashboard/
├── api/
│   ├── app.py
│   ├── db.py
│   ├── params.py               # shared request-param validation (bbox/time/limit)
│   ├── sensor_schema.py        # reading spec: READING_TABLES (storage; ingest+read) + METRIC_META (presentation; served to viewer)
│   ├── observatory.py          # shared anchor-fix + az/el→ECEF reconstruct (constellation + passes)
│   ├── rigctld.py              # stdlib-socket Hamlib rigctld client (radio CI-V control)
│   ├── tile_layers.py
│   └── routes/
│       ├── points.py
│       ├── annotations.py
│       ├── tiles.py
│       ├── sensors.py          # /api/sensors[/<id>/readings] + /api/sensors/series
│       ├── drone.py            # /api/drone/flights (ingest + map-overlay read)
│       ├── docs.py             # /api/docs/* (network-docs vault reader: tree + raw markdown)
│       ├── globe.py            # /api/constellation (3D reconstruction; /globe is SPA-served)
│       ├── passes.py           # /api/passes (pass prediction; /passes is SPA-served)
│       ├── obd.py              # /api/obd* (OBD-II telemetry read)
│       ├── radio.py            # /api/radio/* (Icom ID-5100A CI-V control via rigctld; /radio is SPA-served)
│       ├── status.py           # /api/status (Home glance aggregate read)
│       ├── status_gpsd.py      # /api/gpsd/status + /api/gpsd/sky (Systems → gpsd drill-in + skyplot)
│       └── status_ntp.py       # /api/ntp (Systems → ntp drill-in)
├── common/                     # shared core library (imported across api/tools/processor)
│   ├── gpsd.py                 # short-lived gpsd snapshot query + constellation/device helpers
│   ├── satgeo.py               # az/el→ECEF reconstruction + GMST/ECI frame geometry + on-sky angular sep
│   ├── orbits.py               # inertial-frame orbit fit + propagation + pass finder
│   ├── satcat.py               # CelesTrak SATCAT metadata fetch/cache (NORAD-keyed) for sat identity
│   ├── obd.py                  # speed-density fuel-rate derivation + drive integration (read-time, pure)
│   ├── proc.py                 # subprocess + systemctl (is-active) helpers
│   ├── checks.py               # PASS/FAIL check-runner for the validate tools
│   └── cli.py                  # run_cli/run_click — tools' Ctrl+C → "Interrupted." exit 130
├── logger/
│   └── gps_logger.py
├── processor/                  # processed-tier deriver (tails raw → track_points/track_events)
│   ├── gps_processor.py
│   └── simplify.py             # shared track geometry + Reumann–Witkam (processor + drone importer)
├── sensors/                    # Pi-side sensor readers (publish to the MQTT bus)
│   ├── bme680.py               # --fake pipeline harness (BME680 now lives on an ESP32 node)
│   ├── obd_reader.py           # engine-gated OBD-II reader → sensors/van/obd (NOT obd.py — shadows the obd lib)
│   ├── victron_reader.py       # Victron Venus GX → sensors/house/victron (two brokers; keepalive + staleness watchdog)
│   └── system_reader.py        # Pi host metrics (cpu/mem/disk/temp/throttle) → sensors/pi/system (stdlib /proc + vcgencmd)
├── mqttbus/                    # broker-side consumers + shared MQTT helpers
│   ├── topics.py
│   ├── client.py
│   └── ingest.py
├── firmware/                   # ESPHome configs for remote ESP32 sensor nodes
│   ├── cabin-bme680.yaml       # XIAO ESP32-C6 + BME680 (BSEC2 IAQ)
│   ├── README.md
│   └── secrets.yaml.example    # copy to secrets.yaml before flashing
├── web/                        # Van OS SPA source (Svelte 5 + Vite + TS) → builds to static/dist/
│   ├── package.json, vite.config.ts, tsconfig*.json
│   └── src/
│       ├── App.svelte, main.ts, app.css
│       ├── lib/
│       │   ├── Shell.svelte, router.svelte.ts, routes.ts   # nav shell + client router
│       │   ├── api.ts          # typed JSON API client
│       │   ├── geo.ts          # pure geo/format helpers
│       │   ├── map.ts          # MapView MapLibre façade (npm maplibre/pmtiles)
│       │   ├── mapHost.ts      # persistent keep-alive map host (alive across routes)
│       │   ├── timestrip.ts    # canvas sub-range timeline island (density + stops + brush)
│       │   ├── labels.ts       # POI/label GL-style controls (vector base)
│       │   ├── drone.ts        # drone overlay controller (lazy-imports overlay3d)
│       │   ├── overlay3d.ts    # three.js elevated-line custom MapLibre layer (drone tracks)
│       │   ├── globe.ts, skyplot.ts, sensors.ts            # view renderers/helpers
│       │   ├── docs.ts         # network-docs render: markdown-it + lazy mermaid + link resolution
│       │   └── stores/         # selection (global time axis) · annotations · layers (map-local)
│       └── views/              # Home, Map (+Timeline/TimePicker/Layers/Marks/Inspect/Annotations*), Systems, Trends, Docs, Sky, Globe, Skyplot, Ntp, Gpsd, Radio
├── static/
│   ├── dist/                   # committed SPA build — Flask serves index.html + assets/
│   ├── img/tile-error.png
│   ├── dev-terrain.html        # standalone terrain-preview dev tool (vendored maplibre/pmtiles)
│   └── vendor/
│       ├── basemap/            # Protomaps style.json + glyphs + sprite (data, served as-is)
│       └── maplibre/, pmtiles/ # legacy: dev-terrain.html only (the SPA uses npm); retire with it
├── tools/
│   ├── precache.py
│   ├── fetch_terrain_tiles.py  # Mapzen Terrarium → MBTiles (asyncio+httpx)
│   ├── regions.py              # shared Region dataclass + REGIONS table
│   ├── gpsd_setup.py
│   ├── gpsd_validate.py
│   ├── ntp_setup.py
│   ├── ntp_validate.py
│   ├── obd_probe.py            # OBD-II Phase-0 connectivity probe (plans/obd-platform-plan.md)
│   ├── civ_probe.py            # Icom CI-V Phase-0 connectivity probe, stdlib-only (plans/radio-platform-plan.md)
│   ├── import_drone.py         # DJI drone telemetry importer (.claude/modules/drone.md)
│   ├── passes_validate.py      # backtest pass prediction vs held-out observations (self-consistency)
│   ├── tle_validate.py         # backtest derived orbits vs CelesTrak TLEs+SGP4 (absolute, dev-time)
│   └── fetch_satcat.py         # fetch/cache CelesTrak SATCAT satellite metadata
├── deploy/
│   ├── gps-dashboard.service
│   ├── gps-logger.service
│   ├── gps-processor.service
│   ├── mosquitto.conf
│   ├── mqtt-ingest.service
│   ├── sensor-bme680.service
│   ├── sensor-obd.service       # enabled-gated OBD reader unit (node van, /dev/ttyUSB0)
│   ├── sensor-victron.service   # enabled-gated Victron reader unit (node house; secret via /etc/default/gps-victron)
│   ├── sensor-pi.service        # Pi host-metrics reader unit (node pi; enabled by default — no hardware/secret/gating)
│   ├── gps-drone-sync.service   # timer-driven DJI footage import (Pi → NAS container)
│   ├── gps-drone-sync.timer
│   ├── radio-control.service    # enabled-gated rigctld (Icom ID-5100A CI-V; /dev/icom-civ)
│   ├── exiftool.Dockerfile      # pinned ExifTool ≥13.x image, built on the NAS
│   ├── chrony-gps-only.conf
│   ├── chrony-gps-pps.conf
│   ├── 99-gps-dongle.rules
│   └── 99-icom-civ.rules        # pins the CI-V cable (WCH CH343 1a86:55d3) → /dev/icom-civ
├── plans/                      # active/in-flight plans (landed ones fold into .claude/modules/)
├── reference/                  # vendored equipment manuals/datasheets (PDF + grep-able .txt) for off-grid lookup
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

# Backtest satellite-pass prediction against held-out observations (self-consistency, on Pi)
uv run tools/passes_validate.py --db /mnt/nvme/data/gps_history.db -v

# Backtest derived orbits against CelesTrak TLEs + SGP4 (absolute; needs internet + a DB snapshot)
uv run tools/fetch_satcat.py                                    # cache satellite metadata (one-time/weekly)
uv run tools/tle_validate.py --db ./gps_snap.db --hours 48 -v   # snapshot: ssh Pi '.backup' then scp

# Sensor pipeline (MQTT — needs a broker; PYTHONPATH set so scripts find the packages)
PYTHONPATH=. uv run mqttbus/ingest.py                       # ingest subscriber
PYTHONPATH=. uv run sensors/bme680.py --fake --node cabin   # fake publisher — pipeline test harness
PYTHONPATH=. uv run sensors/bme680.py --node cabin          # (legacy) Pi-attached I2C BME680; the live BME680 is the ESPHome node

# Inspect the database
sqlite3 "$GPS_DB_PATH" "SELECT * FROM gps_points ORDER BY id DESC LIMIT 10;"
sqlite3 "$GPS_DB_PATH" "SELECT * FROM annotations;"
```

### Tests

```bash
uv run pytest                          # full suite
uv run pytest tests/test_simplify.py   # one module
```

`pytest` is a dev dependency (`[dependency-groups].dev`), kept out of the runtime/offline install path. `tests/` covers the load-bearing pure logic and the API read paths:

- **Pure logic** — track simplification (`processor/simplify.py`), canonical-timestamp ordering (`api/db.py`), request-param validation (`api/params.py`), the gpsd constellation resolver, the check-runner, the observatory geometry (`common/orbits` fit + propagation, `common/satgeo` az/el→ECEF, `common/satcat` parsing), the logger's SKY-row builder, OBD speed-density fuel derivation (`common/obd`), the OBD/Victron reader logic, the rigctld TCP client, and the MQTT ingest writer.
- **Flask client against a temp SQLite DB** (`tests/conftest.py`) — `/api/points` size-aware decimation (C17), `/api/constellation`, `/api/passes`, `/api/obd/economy`, the radio routes, and the docs reader (`/api/docs/*`: tree, file fetch, traversal/non-`.md` rejection).
- **Backtest tools** — the pure helpers in `passes_validate` and `tle_validate`.

### Linting & formatting

```bash
uv run ruff check .            # lint
uv run ruff check --fix .      # lint + autofix
uv run ruff format .           # format
```

`ruff` is a dev dependency, same offline carve-out as pytest. Config lives in `[tool.ruff]` (pyproject.toml): `line-length = 100`, single-quote formatting (the codebase is ~80% single-quoted), and a lean lint set (`E`, `F`, `W`, `I`, `UP`, `B`) — real bugs + modern idioms, low noise. Grow the rule set there as needed.

### Type checking

```bash
uv run mypy .                  # type check (must be clean)
```

`mypy` (+ `types-requests`) is a dev dependency, same offline carve-out as pytest/ruff. Config lives in `[tool.mypy]` (pyproject.toml) and runs **strict core / lenient rest**: a lenient global baseline (real errors in annotated code, untyped function bodies left unchecked) with a strict per-module override (`disallow_untyped_defs`/`disallow_any_generics`/…) on the load-bearing, well-typed core — `processor.*`, `common.*`, `logger.*`, `api.db`, `api.params`. Untyped libs (`bme680`, `obd`, `paho.mqtt`) are `ignore_missing_imports`. Ratchet the strict surface outward over time: next `disallow_untyped_calls`/`disallow_untyped_decorators`, then widen the strict module list to `api.routes.*`/`tools.*`/`sensors.*`/`mqttbus.*` (the routes mainly need handler return types).

## Offline Constraint

All runtime dependencies must work without internet. Frontend libraries are npm deps that Vite **bundles into the committed `static/dist/`** (the bundle is offline; the Pi never builds — rebuild + commit before pushing); basemap *data* assets stay in `static/vendor/basemap/`. Python packages install from `uv.lock` at deploy time — no network needed after `uv sync`. The project itself is an editable-installed package (hatchling `[build-system]` in `pyproject.toml`, flat-layout packages enumerated there), so `uv sync` also *builds* it — the hatchling build backend must be in the Pi's uv cache for an offline deploy (cached automatically on the first online `uv sync`). That editable install is what lets any script (`uv run tools/foo.py`) import `common`/`api`/`processor` without a `sys.path` shim. The vector OSM basemap renders fully offline (bundled MapLibre/pmtiles + the local PMTiles archive); USGS raster renders from its on-disk cache, and the tile proxy only reaches upstream when online.

A few runtime deps are **system packages** that work offline once installed but aren't carried by `uv sync` — install them on the Pi while online (one-time): `libhamlib-utils` for the radio `rigctld` service, and the udev rules (`99-gps-dongle.rules`, `99-icom-civ.rules`), which the deploy hook does **not** copy (it installs only `deploy/*.service`/`*.timer`). This matches the project's reading of the offline constraint: it governs *runtime* off-grid correctness, not avoiding cacheable dev-time/system installs.

Development happens with internet available. Building the vector PMTiles archive, pre-caching USGS tiles, and vendoring assets are intentional prep steps before going off-grid.
