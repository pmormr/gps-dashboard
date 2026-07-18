# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A GPS history browser for a Raspberry Pi installed in a van, serving a local network (LAN) that is frequently off-grid with no internet access. The Pi logs GPS data continuously; this app is the interface for reviewing, tagging, and analyzing that data.

Users connect via phone or laptop over the van's WiFi. No authentication is required — the LAN is trusted.

## Documentation layout

This file is the architectural map and router: base architecture + pointers. Landed subsystem detail lives in `.claude/modules/` (`frontend`, `basemaps`, `hardware`, `processor`, `sensors`, `observatory`, `drone`, `phone`, `places`); **active/in-flight** plans live in `plans/` (`motion-imu`, `radio-platform`, `meshtastic-platform`, `sensor-ideas`, `navigation`, `trip-planner`). Keep all of it to **current state, critical traps, and eliminated pathways** — the back-and-forth that produced a decision belongs in git history, not here. When a plan lands, fold its durable bits into the relevant module and drop the plan. The same rule governs code comments: when a plan lands, comments state the resulting invariant in place — plan/phase codenames ("Phase 3", "C7") dangle once the plan file is dropped. Pointers to *active* plan files are fine.

`reference/` holds vendored equipment docs (vendor manuals, datasheets) plus captured device-capability dumps (e.g. the van's supported-PID set) for hardware we may need to consult off-grid — committed rather than gitignored so they ride to the headless Pi. Alongside each PDF, commit a `pdftotext -layout` extraction (same basename, `.txt`) so the doc stays grep-able over SSH without poppler installed on the Pi.

## Deployment

Two systemd services run on the Pi: `gps-logger` (writes GPS data) and `gps-dashboard` (serves the web app). Both are managed via a bare git repo with a post-receive hook.

```bash
# Commit and push to both GitHub and Pi in one step (preferred)
git push all main
```

The hook runs `uv sync` (which also builds the project as an editable install — see Offline Constraint), then restarts services based on what changed. It always restarts `gps-dashboard` and (if enabled) `mqtt-ingest` and `gps-processor`; each enabled sensor reader (`sensor-obd`/`-victron`/`-pi`/`-openwrt`/`-dahua`/`-fridge`) restarts when `sensors/` or its own unit changed, `radio-control` when its unit changed, `radio-recorder` when `radio/` or its own unit changed, `mediamtx` when its unit or `deploy/mediamtx.yml` changed, `radio-stream` when its unit changed — these restart branches are per-unit blocks in the hook, so a brand-new service needs its block added on the Pi. When any `deploy/` file changed it reinstalls all unit files (glob) into `/etc/systemd/system/` and `daemon-reload`s — so editing a service's env var (e.g. `GPS_TERRAIN_PMTILES_PATH`) deploys on push with no manual `systemctl` step. `gps-logger` restarts only if `logger/` (or its unit) changed, to avoid GPS data gaps; `mosquitto` restarts only on its own config changes; `gps-drone-sync` and `gps-db-backup` are timer-driven oneshots, not restarted (their timers are idempotently re-enabled on every push). The `pi` remote points to `pmorgan@192.168.42.178:/mnt/nvme/gps-dashboard.git`.

App files live on an NVMe drive mounted at `/mnt/nvme`:
- `/mnt/nvme/gps-dashboard.git` — bare repo (deploy target)
- `/mnt/nvme/gps-dashboard` — working tree (overwritten by deploys)
- `/mnt/nvme/data/gps_history.db` — database (persists across deploys)
- `/mnt/nvme/data/places.db` — places-tier sidecar DB, ATTACHed by every `get_connection()`; path derives beside the main DB (`GPS_PLACES_DB_PATH` overrides). Rebuildable from public sources, deliberately **outside** the backup path (places.md)
- `/mnt/nvme/backup/gps_history.snap.db` — consistent DB snapshot, refreshed 6-hourly by `gps-db-backup.timer` and pushed to rex-nas `/volume1/backups/gps-dashboard/` when reachable (retention + restore procedure → `tools/backup_db.py` docstring)
- `/mnt/nvme/data/radio-audio/` — VOX-captured transmission WAVs, written by `radio-recorder` with its own retention pruner (rows outlive audio); rebuild-worthless ephemera, deliberately outside the backup path
- `/mnt/nvme/mediamtx/mediamtx` — MediaMTX static binary (manual install; its config is `deploy/mediamtx.yml`, read from the working tree so it deploys on push)
- `/mnt/nvme/cache/tiles/` — raster (USGS) tile cache (persists across deploys)
- `/mnt/nvme/tiles/northamerica.pmtiles` — vector OSM basemap archive, ~33 GB (persists across deploys)
- `/mnt/nvme/tiles/northamerica-terrain.pmtiles` — terrain (Mapzen Terrarium) PMTiles archive, ~105 GB (persists across deploys)
- `/mnt/nvme/paul-network-docs.git` + `/mnt/nvme/paul-network-docs` — the network-docs vault, synced as its **own** bare repo + post-receive checkout (the same pattern as gps-dashboard, but a separate repo). Push from the local `../paul-network-docs` repo with `git push pi main`; the Docs tab reads the checkout via `GPS_NETWORK_DOCS_PATH` **and edits it** (saves auto-commit onto the bare repo's `main` via `GPS_NETWORK_DOCS_GIT_DIR` — the no-commits-on-the-Pi rule below is gps-dashboard's, not this vault's; `git pull pi main` before pushing from the laptop). The repo's `.gitignore` keeps secrets/installers out of the sync.

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
- **Processor** (`processor/gps_processor.py`) — standalone, no Flask. Tails raw `gps_points` by a persisted id cursor and derives the processed tier (`track_points` + `track_events`) the frontend reads. Idempotent and fully rebuildable from raw; never writes raw. Enabled-gated service. (Online denoise — software static-hold stops (accuracy-weighted mean) + Reumann–Witkam moving simplification + per-fix accuracy gating, emitting `stop_start`/`stop_end` events; the cursor advances only to finalized emits, so an open dwell and the open moving segment stay provisional. The frontend reads it — `/api/points` serves `track_points`. See `.claude/modules/processor.md`.)
- **Web app** (`api/app.py`) — Flask, read-heavy. Serves the frontend, JSON API, tile proxy, and status pages.

### Data Model

SQLite (`gps_history.db`). Core GPS tables:

- `gps_points(id, timestamp, lat, lon, speed, altitude, track, epx, epy, epv, eps, climb, mode)` — raw append-only stream; the per-fix accuracy/quality columns feed the denoise processor. `timestamp` is fixed-width ms UTC (`canonical_timestamp`), uniform across all tiers.
- `annotations(id, name, start_time, end_time, notes)` — pure metadata; no foreign keys. `end_time` nullable: NULL = point-in-time bookmark; non-NULL = range, whose points come from `WHERE timestamp BETWEEN start_time AND end_time` against `gps_points`.
- `marks(key, timestamp)` — two rows max (`start`, `end`); persists live range-construction timestamps across restarts.

Processed/denoise tier — derived from raw by `gps-processor`, fully rebuildable (see `.claude/modules/processor.md`):

- `track_points(...)` — the denoised/simplified points the frontend reads via `/api/points` (`kind` `track`|`stop`, `n_raw`, `importance`, `accuracy`, stop `dwell_*`/`radius`, `src_raw_id`). The processor collapses each parked dwell to one accuracy-weighted point and simplifies moving segments (Reumann–Witkam, `importance` = perpendicular deviation).
- `track_events(...)` — processor-emitted events (stop start/end, mode transitions, …); distinct from the user-curated `annotations`.
- `receiver_metadata(id, timestamp, hdop, vdop, pdop, nsat_used, nsat_seen)` — SKY-sourced DOP + sat counts, written by the logger on a ~5 s throttle; standalone telemetry, not joined into the position path.
- `processing_state(key, value)` — the processor's `last_committed_raw_id` cursor.

Drone telemetry tier — aerial GPS tracks batch-imported from DJI footage by `tools/import_drone.py`, fully rebuildable from the source media (see `.claude/modules/drone.md`):

- `drone_flights(id, model, model_code, first_fix_utc, last_fix_utc, media_path, source_name, n_points, min_lat/min_lon/max_lat/max_lon, imported_at)` — one row per clip. Natural key `(model_code, first_fix_utc)` (no DJI model exposes a serial); `media_path` is the canonical rex-nas path, NULL on an SD-card import for the NAS scan to backfill.
- `drone_track_points(id, flight_id, timestamp, lat, lon, abs_alt, importance)` — the thinned track (Reumann–Witkam, shared via `processor/simplify.py`); canonical ms-UTC puts drone points on the same time axis as `gps_points`. `abs_alt` is MSL metres.

Phone location-history tier — the user's Google Timeline export batch-imported by `tools/import_phone_timeline.py` (full-replace on each run — exports are cumulative), fully rebuildable from the export (see `.claude/modules/phone.md`):

- `phone_paths(id, start_time, end_time, n_points, min_lat/min_lon/max_lat/max_lon, imported_at)` — one row per contiguous `timelinePath` breadcrumb segment (thinning never crosses a time gap).
- `phone_track_points(id, path_id, timestamp, lat, lon, importance, activity_type)` — the thinned breadcrumb (shared Reumann–Witkam); `importance=0` marks segment endpoints; `activity_type` is the covering activity's mode (what the map colors by).
- `phone_visits(...)` / `phone_activities(...)` — the semantic layer (place visits, trip segments); its own tables, **not** `annotations` (which stays user-curated).

Places tier — POIs + event schedules synced by `tools/import_places.py` from four sources (NPS API over WAN; the RIDB full CSV export via `--ridb-zip`; the ~10.7M-row OSM NA extract via `--osm-db`, a transfer DB built off-Pi by `tools/build_osm_pois.py`; the ~800k-row USGS GNIS names layer via `--gnis-zip`, deduped against OSM `gnis:feature_id` tags — full-replace per source), plus the `place_wiki` Wikipedia summary/thumbnail cache (`tools/fetch_wikipedia.py` off-Pi → `--wiki-db`; keyed by wiki id so source merges never orphan it), browsed offline; fully rebuildable. Lives in the **`places.db` sidecar** (ATTACHed as `places_db` by `get_connection()`, kept out of the backup path; no write transaction may span main + sidecar — see `.claude/modules/places.md`):

- `places(id, source, source_kind, source_id, park_code, name, lat, lon, summary, details, synced_at, category, rank)` — one unified row per POI (NPS: `park`|`thingstodo`|`tour`|`visitorcenter`|`campground`|`site`; RIDB adds `recarea`|`facility`|`permit` for the other federal agencies' places; OSM kinds are the primary tag, e.g. `amenity=cafe`). `category` (unified taxonomy) + `rank` (pin-zoom tier, 1 major … 5 search-only) govern all sources through one gate — the decision table is `TAXONOMY` in `tools/build_osm_pois.py`, federal kinds map via `api.db.PLACES_KIND_RANKS`. Columns carry only what queries filter on; display-only structure (tour stops + transcripts, hours, amenities, fees, campsite aggregates; OSM full tags) rides in the `details` JSON. Natural key `(source, source_id)`; lat/lon nullable. RIDB `park_code` is the owning `RecAreaID` (numeric; the UI shows `details.recAreaName`). `places_fts` (FTS5) backs search with **token-prefix** semantics.
- `place_events(...)` + `place_event_dates(event_id, date, time_start, time_end)` — scheduled programs with the source's pre-expanded occurrence list as indexed rows (park-local `YYYY-MM-DD` dates as published, **not** ms-UTC), so "what's on this week" is one range query.

GNSS observatory tier — per-satellite az/el logged for 3D reconstruction + pass prediction; reconstructed/fit on-demand, no rollup (see `.claude/modules/observatory.md`):

- `sat_observations(timestamp, gnssid, svid, az, el, snr, used, health)` — one row per positioned satellite per SKY sweep, on the logger's ~60s throttle; indexed `(gnssid, svid, timestamp)` + `timestamp`. The input the globe reconstructs and pass prediction fits orbits from; standalone telemetry, never joined into the position path.

Radio tier — RX transmissions captured by the `radio-recorder` daemon (design = R8 in `plans/radio-platform-plan.md`):

- `radio_transmissions(id, started_utc, ended_utc, duration_s, freq_hz, mode, dcd_main, peak_dbfs, rms_dbfs, audio_path, lat, lon)` — one row per VOX-gate opening; the WAV lives under `/mnt/nvme/data/radio-audio/` and the retention pruner NULLs `audio_path` but keeps the row. `freq_hz`/`mode` are a rigctld snapshot of the **active main band** while the audio is SP1's A+B mix — `dcd_main` marks that tag's confidence; `lat`/`lon` snap from the latest raw fix (NULL when stale).

The same DB also holds the sensor-platform tables (`sensors`, `bme680_readings`, `obd_readings`, `victron_readings`, `system_readings`, `openwrt_readings`, `nvr_readings`, `camera_readings`, `fridge_readings`, `fridge_history`, `alarm_rules`, `alarm_events`) — see the Sensor Platform section below.

### API Endpoints

Signatures + purpose only — full request/response behavior lives in the route files (`api/routes/`, docstrings) and the subsystem modules.

- `GET /api/status` — Home glance aggregate (fix, house power, OBD + link state, cabin IAQ, GNSS health, service states)
- `GET /api/points?start=&end=&limit=&bbox=` — trail/history from the processed tier (`track_points`), size-aware decimated: stops always kept, moving vertices fill the `limit` budget by `importance` (`truncated` ⇒ moving loss only)
- `GET /api/points/latest` — most-recent **raw** fix (the live dot reads `gps_points`, not the processed tier)
- `GET /api/points/recent?minutes=&limit=` — raw trailing window, stride-decimated (the Drive breadcrumb seed; the processed tier would lag the processor's cursor)
- `GET/POST/PATCH/DELETE /api/annotations[/:id]` — user-curated bookmarks/ranges (`end_time` NULL = point bookmark)
- `GET/POST /api/annotations/mark` — persisted `start`/`end` marks (live range construction survives reloads)
- `GET /tiles/osm.pmtiles` · `GET /tiles/terrain.pmtiles` — vector basemap + terrain DEM archives with HTTP range support (basemaps.md)
- `GET /tiles/<layer>/{z}/{x}/{y}.png` — raster (USGS) tile proxy/cache; `?refresh=1` background-revalidates (basemaps.md)
- `GET /api/sensors` · `GET /api/sensors/:id/readings` · `GET /api/sensors/series` — sensor registry, reading history, and the bucketed multi-metric series backing Trends (sensors.md)
- `GET /api/obd/economy?start=&end=` — per-window drive/fuel summary, derived at read time (`common/obd.py`); pass annotation bounds for per-trip MPG
- `GET /api/fridge/status` · `GET /api/fridge/history?span=` · `POST /api/fridge/{setpoint,power}` — CFX3 control plane: DB snapshot + liveness + cached ranges, stored DC-history reads, and zone setpoint/power writes over DDMP with live read-back; 502 = fridge NAK, 503 = unreachable (sensors.md, `reference/cfx3-ddmp.md`)
- `GET/POST /api/drone/flights` — drone-flight map-overlay read + idempotent LAN ingest (drone.md)
- `GET /api/phone/tracks` · `GET /api/phone/places` — phone-history breadcrumb + semantic-layer reads (phone.md)
- `GET /api/places[/:id]` · `GET /api/places/lookup` · `GET /api/places/events[/:id]` — POI/event browse reads; `q` is FTS token-prefix search ordered exact-name → match → rank → distance-to-`center`, `max_rank` is the pin-zoom gate (a caller obligation, not validated — a broad gate-less bbox'd read scans the whole latitude band instead of the partial indexes), `category`/`kind` filter, `center`+`radius` = near-me circle scope, `facets=1` = kind-refinement counts; list pages group cross-source twins (`twins` refs; places.md), `lookup` resolves a `(source, source_id)` natural key (the basemap tap-through bridge); event dates are park-local `YYYY-MM-DD`, **not** ms-UTC, and every payload carries `synced_at` — the UI wears data age (places.md)
- `GET /api/gpsd/sky` · `GET /api/gpsd/status` · `GET /api/gpsd/live` — live gpsd constellation (feeds the skyplot) + device/fix snapshot (Systems drill-in) + the Drive view's 1 Hz TPV-only fix poll
- `GET /api/constellation` · `GET /api/passes` — logged-observation 3D reconstruction + pass prediction (observatory.md)
- `GET /api/radio/status` · `POST /api/radio/{freq,mode,tone,repeater,level,band,dualwatch}` — ID-5100A readout/control via rigctld, **active main band only** (dualwatch on/off is rig-wide, raw CI-V `16 59`); 502 = rig refusal, 503 = rigctld unreachable
- `GET /api/radio/transmissions` · `GET /api/radio/transmissions/:id/audio` — recorded-transmission log (newest-first keyset paging; `min_s` = the blip filter) + Range-capable WAV playback; `has_audio` false = the retention pruner kept the row but dropped the file (the audio route 404s)
- `GET /api/ntp` — chrony/NTP status (Systems drill-in)
- `GET /api/data/status` — offline-data chunk freshness, derived at read time from the `updater/` registry (Systems → `/data` drill-in; read-only until the plan's Phase 2 runner lands — `plans/data-update-plan.md`)
- `GET /api/docs/tree` · `GET/PUT /api/docs/file?path=` — network-docs vault browse + edit-only saves; PUT requires `If-Match` and auto-commits Pi-side (pull before pushing from the laptop)

**SPA routes** — every non-`api`/`tiles`/`static` path returns the Van OS shell (`dist/index.html`) and renders client-side, *not* a server page: `/` (Home) · `/map` · `/drive` · `/places` · `/systems` (+ `/trends`, `/fridge`, `/gpsd`, `/ntp`, `/data` drill-ins) · `/docs` (+ `/docs/<vault-path>` deep links) · `/sky` (+ `/globe`, `/skyplot`, `/passes`) · `/radio`. There are no server-rendered pages left — the app is SPA-only.

### Frontend

**Van OS** — a client-side SPA (Svelte 5 + Vite + TypeScript) in `web/`, built to `static/dist/` (committed) and served by Flask (`api/app.py` catch-all → `dist/index.html` for non-`api`/`tiles`/`static` paths). A persistent nav shell with eight destinations — **Home** (status glance, `/api/status`) · **Map** (`/map`) · **Drive** (`/drive` — follow-camera driving view + destination chevron over the shared map engine) · **Places** (`/places` — master-detail browser/search over the places tier; the map keeps only waypoints) · **Systems** (`/systems` + gpsd/ntp drill-ins) · **Docs** (`/docs` — browses the synced `paul-network-docs` vault) · **Sky** (`/sky` = passes + globe/skyplot) · **Radio** (`/radio`). Mobile-first (bottom tabs on phones, sidebar on desktop). Heavy libs (MapLibre, three) are npm deps, **dynamic-imported** so the main bundle stays small; the basemap data assets stay in `static/vendor/basemap/`. **Build + commit `static/dist/` before `git push all`** — the Pi never builds. Charting lives in the SPA's Trends view (`/trends`); the legacy Jinja `/sensors` page + vendored uPlot were retired. See **`.claude/modules/frontend.md`** for shell/router/stores + per-view detail, and **`.claude/modules/observatory.md`** for the globe/passes/skyplot subsystem.

### Basemaps & Terrain

A single MapLibre map (`MapView`, `web/src/lib/map.ts`) renders two basemaps plus a terrain DEM: **vector OSM** (default — an immutable `northamerica.pmtiles` served at `/tiles/osm.pmtiles`, rendered client-side), **raster USGS** (online proxy + offline disk cache at `/tiles/<layer>/{z}/{x}/{y}.png`), and a **Terrarium terrain DEM** (`/tiles/terrain.pmtiles`) MapLibre drapes the basemap on for 3D. The map's right icon rail drives it directly — **Map style** (base map + labels + 3D terrain) and **Data layers** (drone + phone history) are separate rail panels. See **`.claude/modules/basemaps.md`** for archive paths/env, tile route + cache mechanics, draping, and the precache/terrain-build tooling.

### GPS Logger Detail

Bypasses the Python `gps` library in favor of a direct TCP socket to gpsd on `localhost:2947`. Sends `?WATCH={"enable":true,"json":true}\n`, parses TPV JSON records. Motion-gates raw writes: the full nav rate (~5 Hz) while moving, throttled to ~1 Hz while parked (Doppler speed < 0.5 m/s) — parked 5 Hz is correlated bloat the processor's static-hold collapses anyway. SKY-sourced DOP + sat counts write to `receiver_metadata` on a separate ~5 s throttle. Reconnects automatically on failure with 5s backoff.

Two layers of stall detection: a 30s socket timeout catches a fully frozen gpsd (no bytes at all), and a staleness watchdog forces a reconnect if no valid fix is seen for 120s *while data is still flowing* — the case the socket timeout misses, since gpsd keeps emitting SKY/no-fix TPV. Every 60s it logs a heartbeat with points written, current fix mode, age of the last write, and a breakdown of dropped records by reason (no_fix, no_latlon, bad_range, null_island, stale_time, throttled, json_err), so a silent stall names its own cause in the journal.

### Sensor Platform (MQTT)

A second data stream beyond GPS: sensor readings ingested over a local mosquitto MQTT bus into the **same** SQLite DB, for GPS↔sensor correlation; GPS logging stays off the bus. Seven streams are live, each a reader publishing `sensors/<node>/<type>` through the same ingest into its own `*_readings` table: the cabin BME680 (ESPHome ESP32-C6, BSEC2 IAQ), the van's OBD-II (engine-gated Pi-side reader via an SGW-bypass harness; PID set in `reference/obd-supported-pids.md`), Victron house power, the Pi host itself, the van-edge router (SSH poll), the Dahua NVR + camera fleet (CGI/RPC2), and the Dometic CFX3 fridge (DDMP-over-WiFi poll — plus stored DC power history and a read/write control plane at `/api/fridge/*` + `/fridge`; protocol reference `reference/cfx3-ddmp.md`). Adding a stream is a spec entry (`api/sensor_schema.py`), not a new pipeline. The SPA's Systems and Trends views read the ingested data from the DB. See **`.claude/modules/sensors.md`** for per-stream architecture and the remaining roadmap (*live* MQTT-over-WS push readouts + alarms are still planned).

### Radio Control (CI-V)

Control the van's **Icom ID-5100A** transceiver from the Pi over its CI-V serial bus. A long-lived **`rigctld`** (Hamlib model **3071**) owns the serial port (cable = OPC-478UC clone / WCH CH343 on a udev-pinned `/dev/icom-civ`, 19200 baud, address 0x8C) and exposes Hamlib's TCP text protocol on `127.0.0.1:4532`; the Flask routes (`api/routes/radio.py`) speak that protocol through a stdlib-socket client (`api/rigctld.py`) — **no** Python Hamlib binding, and the daemon-owns-the-port model solves serial contention. The `/radio` page controls the **active main band only** (the backend can't read which VFO is active): freq/mode/S-meter readout + set, CTCSS/DCS tone, and repeater shift/offset (the backend exposes no memory recall). The `radio-control` service is **enabled-gated** (disabled until the cable is wired). Transmission **recording** (audio plane) is built: the enabled-gated `radio-recorder` daemon (`radio/recorder.py`) VOX-gates the Digirig's SP1 capture (the A+B mix) into pre-rolled WAVs + GPS-snapped `radio_transmissions` rows — a capture commits only with ≥ `GPS_RADIO_MIN_LOUD_BLOCKS` (~600 ms) of above-threshold activity, since beep/crackle transients sit at exactly voice level (activity separates, level cannot — plan R9; `tools/radio_vox_replay.py` rescores stored WAVs by the same rule) — re-pinning the C-Media mixer (replug resets it to max gain + AGC ON) and the rig's AF level every session start; the `/radio` page serves the resulting log (list + in-browser playback via `/api/radio/transmissions` — freq/mode tags render dimmed unless `dcd_main=1`; no map surface, by choice). **Live listen** (stream plane): a **MediaMTX** hub (`mediamtx` service; static binary at `/mnt/nvme/mediamtx/`, config `deploy/mediamtx.yml`) fan-outs a continuous Opus stream published by the enabled-gated `radio-stream` ffmpeg unit — listen via VLC/OBS at `rtsp://<pi>:8554/radio` or sub-second in any browser at `http://<pi>:8889/radio` (WebRTC, no STUN needed on the LAN); the hub is also the intended aggregation point for Dahua camera proxying later. Capture is shared with the recorder via an ALSA **dsnoop** PCM (`deploy/asound.conf` → `/etc/asound.conf`, manual install; both daemons open `digirig_shared`, either runs without the other) — the hw codec is single-open, so anything else that captures the Digirig must go through that PCM too. **PTT trap:** RTS on the Digirig serial port hardware-keys TX — nothing may open `/dev/digirig` casually; the guard stack (ModemManager masked, gpsd `USBAUTO=false`, `99-digirig.rules`, `digirig-rts-clear` udev oneshot) is non-negotiable. **Announcements** (TX, Part-97) are a later phase — see **`plans/radio-platform-plan.md`** for the capability map and roadmap.

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
│       ├── data.py             # /api/data/status (offline-data chunk freshness; /data is SPA-served)
│       ├── places.py           # /api/places* (POI/tours/events/hours tier reads)
│       ├── tiles.py
│       ├── sensors.py          # /api/sensors[/<id>/readings] + /api/sensors/series
│       ├── drone.py            # /api/drone/flights (ingest + map-overlay read)
│       ├── fridge.py           # /api/fridge/* (CFX3 setpoint/power writes + status/history reads; /fridge is SPA-served)
│       ├── phone.py            # /api/phone/{tracks,places} (phone-history map-overlay reads)
│       ├── docs.py             # /api/docs/* (network-docs vault: tree + raw markdown + edit PUT w/ auto-commit)
│       ├── globe.py            # /api/constellation (3D reconstruction; /globe is SPA-served)
│       ├── passes.py           # /api/passes (pass prediction; /passes is SPA-served)
│       ├── obd.py              # /api/obd* (OBD-II telemetry read)
│       ├── radio.py            # /api/radio/* (Icom ID-5100A CI-V control via rigctld; /radio is SPA-served)
│       ├── status.py           # /api/status (Home glance aggregate read)
│       ├── status_gpsd.py      # /api/gpsd/status + /api/gpsd/sky + /api/gpsd/live (gpsd drill-in + skyplot + Drive feed)
│       └── status_ntp.py       # /api/ntp (Systems → ntp drill-in)
├── common/                     # shared core library (imported across api/tools/processor)
│   ├── gpsd.py                 # short-lived gpsd snapshot query + constellation/device helpers
│   ├── satgeo.py               # az/el→ECEF reconstruction + GMST/ECI frame geometry + on-sky angular sep
│   ├── orbits.py               # inertial-frame orbit fit + propagation + pass finder
│   ├── satcat.py               # CelesTrak SATCAT metadata fetch/cache (NORAD-keyed) for sat identity
│   ├── obd.py                  # speed-density fuel-rate derivation + drive integration (read-time, pure)
│   ├── ddmp.py                 # Dometic CFX3 DDMP protocol core (framing/topics/codecs + DdmpClient session)
│   ├── humidity.py             # derived moisture channels (dew point, absolute humidity, heat index)
│   ├── timefmt.py              # canonical_timestamp — fixed-width ms-UTC formatter shared across tiers
│   ├── proc.py                 # subprocess + systemctl (is-active) helpers
│   ├── checks.py               # PASS/FAIL check-runner for the validate tools
│   └── cli.py                  # run_cli/run_click — tools' Ctrl+C → "Interrupted." exit 130
├── logger/
│   └── gps_logger.py
├── processor/                  # processed-tier deriver (tails raw → track_points/track_events)
│   ├── gps_processor.py
│   └── simplify.py             # shared track geometry + Reumann–Witkam (processor + drone importer)
├── radio/                      # radio-plane daemons (plans/radio-platform-plan.md)
│   ├── vox.py                  # pure VOX gate math + state machine (clockless, table-tested)
│   ├── paths.py                # audio-root resolution shared by the recorder + the API's audio route
│   └── recorder.py             # VOX-gated Digirig capture → WAV + radio_transmissions rows
├── updater/                    # offline-data chunk manager (plans/data-update-plan.md)
│   ├── chunks.py               # declarative CHUNKS registry + derived status/ordering warnings
│   └── probes.py               # derived-freshness probes (DB slices, archives, caches) — never stored state
├── sensors/                    # Pi-side sensor readers (publish to the MQTT bus)
│   ├── runner.py               # shared reader framework (run_simple_publisher/run_fleet_publisher, LWT status, heartbeat)
│   ├── bme680.py               # synthetic pipeline test harness (the live BME680 is the ESP32 node)
│   ├── obd_reader.py           # engine-gated OBD-II reader → sensors/van/obd (NOT obd.py — shadows the obd lib)
│   ├── victron_reader.py       # Victron Venus GX → sensors/house/victron (two brokers; keepalive + staleness watchdog)
│   ├── system_reader.py        # Pi host metrics (cpu/mem/disk/temp/throttle) → sensors/pi/system (stdlib /proc + vcgencmd)
│   ├── openwrt_reader.py       # van-edge router SSH poll → sensors/van-edge/openwrt (one sh -s round-trip per poll)
│   ├── dahua_reader.py         # Dahua NVR + cams CGI/RPC2 fleet poll → 5 node streams (types nvr + camera)
│   ├── dahua_rpc.py            # minimal Dahua RPC2 JSON client (challenge login, object-style handles)
│   └── fridge_reader.py        # Dometic CFX3 DDMP-over-WiFi poll → sensors/van/fridge
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
│       │   ├── timestrip.ts    # canvas timeline island (density + stops + drag-to-zoom)
│       │   ├── icons.ts        # the one POI icon language (sprite maps + planetiler id codec)
│       │   ├── labels.ts       # basemap pois-layer composer (categories × density × twin suppression)
│       │   ├── drone.ts        # drone overlay controller (lazy-imports overlay3d)
│       │   ├── phone.ts        # phone-history overlay: color-by-mode run-splitting + visit pins + sync
│       │   ├── places.ts       # places overlay: per-kind pin builders + viewport-driven sync (z6 gate)
│       │   ├── overlay3d.ts    # three.js elevated-line custom MapLibre layer (drone tracks)
│       │   ├── globe.ts, skyplot.ts, sensors.ts, radio.ts, fridge.ts  # view renderers/helpers
│       │   ├── live.ts, follow.ts, wakelock.ts  # Drive view: live-fix math · follow-camera policy · screen wake lock
│       │   ├── charts/         # Trends chart components (LayerCake: Trend/Line/Band/axes)
│       │   ├── docs.ts         # network-docs render: markdown-it + lazy mermaid + link resolution
│       │   ├── docsEditor.ts   # Docs edit mode: CodeMirror 6 wrapper (lazy chunk, loaded on Edit)
│       │   └── stores/         # selection (global time axis + zoom history) · track (shared window fetch) · annotations (named windows) · layers (map-local) · places (browse session) · live (1 Hz fix poll + interpolation)
│       └── views/              # Home, Map (+TimeDock/TimePicker/DataLayers/MapStyle/Marks/Inspect/Annotations*/PlaceSheet), Drive, Places (+PlaceDetail/EventDetail shared with the sheet), Systems, Trends, Fridge, Data, Docs, Sky, Globe, Skyplot, Ntp, Gpsd, Radio, NotFound
├── static/
│   ├── dist/                   # committed SPA build — Flask serves index.html + assets/
│   ├── img/                    # tile-error.png + the globe's Earth textures
│   └── vendor/
│       ├── basemap/            # generated theme styles (web/scripts/generate-basemap-styles.mjs) + glyphs + sprites
├── tools/
│   ├── precache.py
│   ├── fetch_terrain_tiles.py  # Mapzen Terrarium → MBTiles (asyncio+httpx)
│   ├── regions.py              # shared Region dataclass + REGIONS table
│   ├── gpsd_setup.py
│   ├── gpsd_validate.py
│   ├── ntp_setup.py
│   ├── ntp_validate.py
│   ├── obd_probe.py            # OBD-II connectivity/bring-up probe (kept as a bus diagnostic)
│   ├── civ_probe.py            # Icom CI-V Phase-0 connectivity probe, stdlib-only (plans/radio-platform-plan.md)
│   ├── digirig_clear_rts.py    # udev-oneshot RTS/DTR clearer — RTS hardware-keys PTT on this port
│   ├── radio_vox_replay.py     # rescore stored captures by the VOX commit rule (--purge deletes)
│   ├── cfx3_probe.py           # CFX3 DDMP survey probe, stdlib-only — history/range topics + --watch + --write-test (reference/cfx3-ddmp.md)
│   ├── openwrt_probe.py        # OpenWrt telemetry-source survey over SSH (kept as a router diagnostic)
│   ├── dahua_probe.py          # Dahua CGI endpoint survey, NVR + cams (kept as a fleet diagnostic)
│   ├── backup_db.py            # DB snapshot + opportunistic rsync to rex-nas + retention (gps-db-backup.timer)
│   ├── import_places.py        # NPS API + RIDB export → places tier (.claude/modules/places.md)
│   ├── build_osm_pois.py       # Geofabrik PBF → OSM POI transfer DB (laptop/NAS only; .claude/modules/places.md)
│   ├── import_drone.py         # DJI drone telemetry importer (.claude/modules/drone.md)
│   ├── import_phone_timeline.py # Google Timeline → phone tier (.claude/modules/phone.md)
│   ├── passes_validate.py      # backtest pass prediction vs held-out observations (self-consistency)
│   ├── tle_validate.py         # backtest derived orbits vs CelesTrak TLEs+SGP4 (absolute, dev-time)
│   └── fetch_satcat.py         # fetch/cache CelesTrak SATCAT satellite metadata
├── deploy/
│   ├── gps-dashboard.service
│   ├── gps-logger.service
│   ├── gps-processor.service
│   ├── mosquitto.conf
│   ├── mqtt-ingest.service
│   ├── sensor-obd.service       # enabled-gated OBD reader unit (node van, /dev/ttyUSB0)
│   ├── sensor-victron.service   # enabled-gated Victron reader unit (node house; secret via /etc/default/gps-victron)
│   ├── sensor-pi.service        # Pi host-metrics reader unit (node pi; enabled by default — no hardware/secret/gating)
│   ├── sensor-openwrt.service   # enabled-gated OpenWrt reader unit (node van-edge; auth = Pi SSH key on the router)
│   ├── sensor-dahua.service     # enabled-gated Dahua fleet reader unit (5 nodes; secret via /etc/default/gps-dahua)
│   ├── sensor-fridge.service    # enabled-gated CFX3 fridge reader unit (node van; CFX_HOST pins the fridge IP)
│   ├── gps-drone-sync.service   # timer-driven DJI footage import (Pi → NAS container)
│   ├── gps-drone-sync.timer
│   ├── gps-db-backup.service    # timer-driven DB backup (snapshot → rsync to rex-nas /volume1)
│   ├── gps-db-backup.timer
│   ├── radio-control.service    # enabled-gated rigctld (Icom ID-5100A CI-V; /dev/icom-civ)
│   ├── radio-recorder.service   # enabled-gated VOX transmission recorder (Digirig SP1 capture)
│   ├── radio-stream.service     # enabled-gated live-listen publisher (dsnoop → Opus → MediaMTX)
│   ├── mediamtx.service         # media hub (RTSP/WebRTC fan-out; binary is a manual NVMe install)
│   ├── mediamtx.yml             # hub config, read from the deploy checkout (edits deploy on push)
│   ├── asound.conf              # dsnoop shared capture for the Digirig (manual /etc install)
│   ├── digirig-rts-clear.service # udev-triggered oneshot: deassert RTS/DTR on /dev/digirig (PTT guard)
│   ├── exiftool.Dockerfile      # pinned ExifTool ≥13.x image, built on the NAS
│   ├── chrony-gps-only.conf
│   ├── chrony-gps-pps.conf
│   ├── 99-gps-dongle.rules
│   ├── 99-icom-civ.rules        # pins the CI-V cable (WCH CH343 1a86:55d3) → /dev/icom-civ
│   └── 99-digirig.rules         # Digirig guard: MM-ignore (RTS keys PTT!) + /dev/digirig + ALSA id
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
PYTHONPATH=. uv run sensors/bme680.py --node cabin          # fake publisher — pipeline test harness

# Inspect the database
sqlite3 "$GPS_DB_PATH" "SELECT * FROM gps_points ORDER BY id DESC LIMIT 10;"
sqlite3 "$GPS_DB_PATH" "SELECT * FROM annotations;"
```

### Tests

```bash
uv run pytest                          # full suite
uv run pytest tests/test_simplify.py   # one module
```

`pytest` is a dev dependency (`[dependency-groups].dev`), kept out of the runtime/offline install path. `tests/` covers three surfaces: the load-bearing pure logic (geometry, timestamps, params, reader/protocol logic), the API read paths via a Flask client against a temp SQLite DB (`tests/conftest.py`), and the backtest tools' pure helpers. The directory is the inventory — keep new load-bearing logic and API reads covered.

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

`mypy` (+ `types-requests`) is a dev dependency, same offline carve-out as pytest/ruff. Config lives in `[tool.mypy]` (pyproject.toml) and runs **strict core / lenient rest**: a lenient global baseline (real errors in annotated code, untyped function bodies left unchecked) with a strict per-module override (`disallow_untyped_defs`/`disallow_any_generics`/…) on the load-bearing, well-typed core — `processor.*`, `common.*`, `logger.*`, `api.db`, `api.params`. Untyped libs (`obd`, `paho.mqtt`, `sgp4`) are `ignore_missing_imports`. Ratchet the strict surface outward over time: next `disallow_untyped_calls`/`disallow_untyped_decorators`, then widen the strict module list to `api.routes.*`/`tools.*`/`sensors.*`/`mqttbus.*` (the routes mainly need handler return types).

## Offline Constraint

All runtime dependencies must work without internet. Frontend libraries are npm deps that Vite **bundles into the committed `static/dist/`** (the bundle is offline; the Pi never builds — rebuild + commit before pushing); basemap *data* assets stay in `static/vendor/basemap/`. Python packages install from `uv.lock` at deploy time — no network needed after `uv sync`. The project itself is an editable-installed package (hatchling `[build-system]` in `pyproject.toml`, flat-layout packages enumerated there), so `uv sync` also *builds* it — the hatchling build backend must be in the Pi's uv cache for an offline deploy (cached automatically on the first online `uv sync`). That editable install is what lets any script (`uv run tools/foo.py`) import `common`/`api`/`processor` without a `sys.path` shim. The vector OSM basemap renders fully offline (bundled MapLibre/pmtiles + the local PMTiles archive); USGS raster renders from its on-disk cache, and the tile proxy only reaches upstream when online.

A few runtime deps are **system packages** that work offline once installed but aren't carried by `uv sync` — install them on the Pi while online (one-time): `libhamlib-utils` for the radio `rigctld` service, `alsa-utils` (`arecord`/`amixer`) for the `radio-recorder` service (usually preinstalled on Raspberry Pi OS — verify), `ffmpeg` + the MediaMTX static binary (`/mnt/nvme/mediamtx/`) for the radio live-listen stream, and the non-unit config files the deploy hook does **not** copy (it installs only `deploy/*.service`/`*.timer`): the udev rules (`99-gps-dongle.rules`, `99-icom-civ.rules`, `99-digirig.rules`) and `asound.conf` (→ `/etc/asound.conf`). This matches the project's reading of the offline constraint: it governs *runtime* off-grid correctness, not avoiding cacheable dev-time/system installs.

Development happens with internet available. Building the vector PMTiles archive, pre-caching USGS tiles, and vendoring assets are intentional prep steps before going off-grid.
