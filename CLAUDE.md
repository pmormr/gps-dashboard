# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A GPS history browser for a Raspberry Pi installed in a van, serving a local network (LAN) that is frequently off-grid with no internet access. The Pi logs GPS data continuously; this app is the interface for reviewing, tagging, and analyzing that data.

Users connect via phone or laptop over the van's WiFi. No authentication is required — the LAN is trusted.

## Documentation layout

This file is the architectural map and router: base architecture + pointers. Landed subsystem detail lives in `.claude/modules/` (`deploy`, `frontend`, `basemaps`, `hardware`, `processor`, `sensors`, `observatory`, `drone`, `phone`, `places`, `radio`, `broadcast`); **active/in-flight** plans live in `plans/` (`motion-imu`, `cameras`, `meshtastic-platform`, `sensor-ideas`, `navigation`, `trip-planner`, `weather` — radar + warnings shipped, P5+ registry growth open — and `ride-with-me`). Keep all of it to **current state, critical traps, and eliminated pathways** — the back-and-forth that produced a decision belongs in git history, not here. When a plan lands, fold its durable bits into the relevant module and drop the plan. The same rule governs code comments: when a plan lands, comments state the resulting invariant in place — plan/phase codenames ("Phase 3", "C7") dangle once the plan file is dropped. Pointers to *active* plan files are fine.

`reference/` holds vendored equipment docs (vendor manuals, datasheets) plus captured device-capability dumps (e.g. the van's supported-PID set) for hardware we may need to consult off-grid — committed rather than gitignored so they ride to the headless Pi. Alongside each PDF, commit a `pdftotext -layout` extraction (same basename, `.txt`) so the doc stays grep-able over SSH without poppler installed on the Pi.

## Deployment

Code reaches the Pi through a bare git repo + post-receive hook: the hook runs `uv sync` and restarts only what changed (`gps-logger` only when `logger/` changed, to avoid GPS data gaps; timer-driven oneshots are never restarted). The per-unit restart matrix, NVMe path inventory, remote setup, and log/status commands are in **`.claude/modules/deploy.md`**.

```bash
# Commit and push to both GitHub and Pi in one step (preferred)
git push all main
```

**Never commit directly on the Pi.** All commits go local → push to both remotes. Direct Pi commits cause history divergence requiring force-pushes to fix.

Production topology is **nginx (:443 + :80, LAN front door) → waitress (Flask app, `127.0.0.1:8000`)** at `https://van.pmormr.com/`, with plain `http://192.168.42.178/` deliberately un-redirected as the off-grid fallback. TLS is a hand-managed on-box conf, **not** repo-deployed; the nginx config itself is (`deploy/gps-dashboard.nginx.conf`). What nginx direct-serves vs proxies, the WHEP proxy, and the local-dev env (`GPS_DEV=1`, `GPS_BIND_HOST`/`GPS_BIND_PORT`) are in deploy.md.

## Architecture

### Processes

- **Logger** (`logger/gps_logger.py`) — standalone script, no Flask. Reads from gpsd via TCP socket on `localhost:2947`. The only writer of raw (`gps_points` + `receiver_metadata`); position writes are motion-gated (5 Hz moving / ~1 Hz parked).
- **Processor** (`processor/gps_processor.py`) — standalone, no Flask. Tails raw `gps_points` by a persisted id cursor and derives the processed tier (`track_points` + `track_events`) the frontend reads. Idempotent and fully rebuildable from raw; never writes raw. Enabled-gated service. (Online denoise — software static-hold stops (accuracy-weighted mean) + Reumann–Witkam moving simplification + per-fix accuracy gating, emitting `stop_start`/`stop_end` events, and breaking the trail with a `gap` event across a hole in the fix stream; the cursor advances only to finalized emits, so an open dwell and the open moving segment stay provisional. The frontend reads it — `/api/points` serves `track_points`. See `.claude/modules/processor.md`.)
- **Web app** (`api/app.py`) — Flask, read-heavy, served by **waitress** behind **nginx** (see Deployment). Serves the frontend, JSON API, tile proxy, and status pages.

### Data Model

SQLite (`gps_history.db`); all schemas live in `api/db.py`. Every `timestamp` is fixed-width ms UTC (`canonical_timestamp`) — one uniform time axis across all tiers. Core tables:

- `gps_points` — raw append-only stream, written only by the logger; per-fix accuracy/quality columns feed the denoise processor.
- `annotations` — user-curated bookmarks/ranges; pure metadata, no foreign keys (`end_time` NULL = point-in-time bookmark, non-NULL = time range against `gps_points`).

Every other tier is **derived and fully rebuildable** from raw data or an external source; each is documented in its module:

- **Processed/denoise** (`track_points`, `track_events`, `receiver_metadata`, `processing_state`) — what the frontend reads, derived by `gps-processor` (processor.md).
- **Drone** (`drone_flights`, `drone_track_points`) — DJI telemetry imported from footage (drone.md).
- **Phone** (`phone_paths`, `phone_track_points`, `phone_visits`, `phone_activities` + `owntracks_points`) — Google Timeline import (full-replace per run) + the live OwnTracks tier pulled 5-minutely from the home Recorder (phone.md).
- **Places** (`places`, `places_fts`, `place_events`, `place_event_dates`, `place_wiki`) — POI/event tier in the **`places.db` sidecar** (ATTACHed by every `get_connection()`, outside the backup path; no write transaction may span main + sidecar). Event dates are park-local `YYYY-MM-DD`, **not** ms-UTC (places.md).
- **GNSS observatory** (`sat_observations`) — per-satellite az/el telemetry; never joined into the position path (observatory.md).
- **Radio** (`radio_transmissions`) — VOX-captured RX + operator-TX log; the retention pruner drops audio but keeps rows (radio.md).
- **Sensor platform** (`sensors`, the `*_readings` tables, `fridge_history`, `alarm_rules`, `alarm_events`) — MQTT-ingested streams (sensors.md).

### API Endpoints

One route file per API surface in `api/routes/` (docstrings carry request/response behavior); each subsystem module explains its surface. Trap: `/api/points/latest` serves the latest **raw** fix — the live dot reads `gps_points`, not the processed tier.

### Frontend

**Van OS** — a client-side SPA (Svelte 5 + Vite + TypeScript) in `web/`, built to `static/dist/` (committed) and served by Flask (`api/app.py` catch-all → `dist/index.html` for non-`api`/`tiles`/`static` paths). A persistent nav shell with twelve destinations — **Home** (status glance, `/api/status`) · **Map** (`/map`) · **Drive** (`/drive` — follow-camera driving view + destination chevron over the shared map engine) · **Places** (`/places` — master-detail browser/search over the places tier; the map keeps only waypoints) · **Systems** (`/systems` — a live van-subsystem dashboard over sensors/fridge/trends tiles) · **Diagnostics** (`/diagnostics` — a service/infra health hub over the time/gps/logs/media/data drill-ins) · **Trends** (`/trends` — the graph explorer) · **Docs** (`/docs` — browses the synced `paul-network-docs` vault) · **Sky** (`/sky` = passes + globe/skyplot) · **Radio** (`/radio`) · **Weather** (`/weather` — an animated, scrubbable national radar loop over the shared map + a warnings vector layer; the capture-while-online/play-offline tier, `plans/weather-plan.md`) · **Broadcast** (`/broadcast` — event-day feed config + a two-sides monitor wall over the van + cloud media hubs). (Cameras adds a thirteenth tab — still plan-tracked, `plans/cameras-plan.md`.) A tab with sub-destinations uses one shared pattern (`SECTIONS` registry + shell-level `SectionNav` + `SectionHub` tiles). Mobile-first *and* desktop-first (bottom tabs on phones with a "More" overflow past five, a collapsible sidebar on desktop; desktop content is capped/multi-column — see `frontend.md` Desktop layout). Heavy libs (MapLibre, three) are npm deps, **dynamic-imported** so the main bundle stays small; the basemap data assets stay in `static/vendor/basemap/`. **Build + commit `static/dist/` before `git push all`** — the Pi never builds. Charting lives in the SPA's Trends view (`/trends`); the legacy Jinja `/sensors` page + vendored uPlot were retired. See **`.claude/modules/frontend.md`** for shell/router/stores + per-view detail, and **`.claude/modules/observatory.md`** for the globe/passes/skyplot subsystem.

### Basemaps & Terrain

A single MapLibre map (`MapView`, `web/src/lib/map.ts`) renders two basemaps plus a terrain DEM: **vector OSM** (default — an immutable `northamerica.pmtiles` served at `/tiles/osm.pmtiles`, rendered client-side), **raster USGS** (online proxy + offline disk cache at `/tiles/<layer>/{z}/{x}/{y}.png`), and a **Terrarium terrain DEM** (`/tiles/terrain.pmtiles`) MapLibre drapes the basemap on for 3D. The **shell-owned** right icon rail drives it (`MAP_CHROME` in `routes.ts`: Map gets **Map style** (base map + labels + 3D terrain) and **Data layers** (drone + phone history) as separate panels, Drive and Weather the style button alone); style prefs persist across reloads (`lib/mapPrefs.ts`). See **`.claude/modules/basemaps.md`** for archive paths/env, tile route + cache mechanics, draping, and the precache/terrain-build tooling.

### GPS Logger Detail

Bypasses the Python `gps` library in favor of a direct TCP socket to gpsd on `localhost:2947`. Sends `?WATCH={"enable":true,"json":true}\n`, parses TPV JSON records. Motion-gates raw writes: the full nav rate (~5 Hz) while moving, throttled to ~1 Hz while parked (Doppler speed < 0.5 m/s) — parked 5 Hz is correlated bloat the processor's static-hold collapses anyway. SKY-sourced DOP + sat counts write to `receiver_metadata` on a separate ~5 s throttle. Reconnects automatically on failure with 5s backoff.

Two layers of stall detection: a 30s socket timeout catches a fully frozen gpsd (no bytes at all), and a staleness watchdog forces a reconnect if no valid fix is seen for 120s *while data is still flowing* — the case the socket timeout misses, since gpsd keeps emitting SKY/no-fix TPV. Every 60s it logs a heartbeat with points written, current fix mode, age of the last write, and a breakdown of dropped records by reason (no_fix, no_latlon, bad_range, null_island, stale_time, throttled, json_err), so a silent stall names its own cause in the journal.

### Sensor Platform (MQTT)

A second data stream beyond GPS: sensor readings ingested over a local mosquitto MQTT bus into the **same** SQLite DB, for GPS↔sensor correlation; GPS logging stays off the bus. Seven streams are live, each a reader publishing `sensors/<node>/<type>` through the same ingest into its own `*_readings` table: the cabin BME680 (ESPHome ESP32-C6, BSEC2 IAQ), the van's OBD-II (engine-gated Pi-side reader via an SGW-bypass harness; PID set in `reference/obd-supported-pids.md`), Victron house power, the Pi host itself, the van-edge router (SSH poll), the Dahua NVR + camera fleet (CGI/RPC2), and the Dometic CFX3 fridge (DDMP-over-WiFi poll — plus stored DC power history and a read/write control plane at `/api/fridge/*` + `/fridge`; protocol reference `reference/cfx3-ddmp.md`). Adding a stream is a spec entry (`api/sensor_schema.py`), not a new pipeline. The SPA's Systems and Trends views read the ingested data from the DB. See **`.claude/modules/sensors.md`** for per-stream architecture and the remaining roadmap (*live* MQTT-over-WS push readouts + alarms are still planned).

### Radio Control (CI-V)

Control the van's **Icom ID-5100A** transceiver from the Pi. The subsystem is four planes across two physical interfaces — **control** (CI-V serial, via a long-lived Hamlib `rigctld` the Flask routes drive over a stdlib socket), **record** (VOX-gated Digirig capture → GPS-joined `radio_transmissions` log), **stream** (a MediaMTX hub fanning out a live Opus feed — `rtsp://<pi>:8554/radio`, browser WebRTC at `:8889/radio`), and **transmit** (an operator-clicked `/radio` console: filesystem soundboard + espeak-ng/piper TTS). All three services (`radio-control`/`radio-recorder`/`radio-stream`) are **enabled-gated** (dormant until the hardware is wired). Attended-only (no scheduler → no §97.109/§97.113 exposure); callsign ID is operator-manual (KC3HEU).

**PTT trap (non-negotiable):** RTS on the Digirig serial port (`/dev/digirig`) hardware-keys TX — nothing may open it casually. The guard stack (ModemManager masked, gpsd `USBAUTO=false`, `99-digirig.rules`, `digirig-rts-clear` udev oneshot) is load-bearing; the only intentional RTS assert is the `keyed_tx_rts` transmit path (the one keyer that works in cross-band Repeater Mode, where all CI-V is NAK'd). **Cross-band Repeater Mode is touchscreen-only in every direction** — CI-V can't enter, exit, or power-cycle out of it.

See **`.claude/modules/radio.md`** for all four planes, the CI-V capability map + traps (VFO-select-before-tune, DDL-first deploys, freq inference), the levels/calibration model, the R1–R11 design decisions (code anchors to those numbers), and the deferred items (Y-split/pad purchase, piper voice, Dahua proxy).

### Broadcast (event streaming)

The **Broadcast** tab (📡 `/broadcast`) centralizes config + secrets + **two-sides live status** for every MediaMTX feed across the **van** hub (`pmpi1`, LAN, no auth) and the **cloud** hub (`vps202051`, public/authed, reached over a direct van↔cloud **WireGuard control tunnel** — video stays public/untunneled). One declarative registry (`broadcast/feeds.py`) drives both the copy-ready config reference (offline-safe; secrets interpolated server-side from `/etc/default/gps-broadcast`, never the public repo) *and* a broadcaster-style **monitor wall**: each feed's ingest (is a real source sending?) vs egress (is OBS pulling?) read from the control API, plus per-feed JPEG snapshots and the raw hub log. The wall exists to surface the masked failure — a dead source whose `alwaysAvailable` path keeps serving a STANDBY loop so OBS looks fine. `tools/gen_mediamtx_paths.py` generates the van hub's paths from the registry (drift-tested).

See **`.claude/modules/broadcast.md`** for both hubs, the two-sides status model + the live-hub traps (`source.id` signal, STANDBY = `source:null`, the `'MPEG-4 Audio'` codec string), the WG tunnel + off-repo cloud agent, and the B1–B11 decisions (code anchors to those numbers).

## Hardware Notes

Current GPS: a u-blox **NEO-M9N** read by gpsd as **UBX binary** on `/dev/ttyAMA0` @ 38400, 5 Hz, 4-constellation; PPS on GPIO 4 → `/dev/pps0` drives chrony stratum 1. **Baud trap:** keep gpsd at the module's *reset-default* baud (38400), never a higher forced rate — a power-drained config revert once desynced gpsd and stalled the logger silently for days. Full module/PPS/baud detail, legacy hardware, and the gpsd/NTP setup + validation tooling are in **`.claude/modules/hardware.md`**.

## Tool Scripts

All scripts in `tools/` must handle `KeyboardInterrupt` gracefully — print `"\nInterrupted."` and exit with code 130. Never let Ctrl+C produce a traceback. For scripts using `ThreadPoolExecutor`, catch `KeyboardInterrupt` inside the `as_completed` loop, cancel pending futures, print partial stats, and exit 130.

## Commands

```bash
# Run the web app locally (module form — api/app.py uses package imports)
uv run python -m api.app

# Sensor pipeline (MQTT — needs a broker; PYTHONPATH set so scripts find the packages)
PYTHONPATH=. uv run mqttbus/ingest.py                       # ingest subscriber
PYTHONPATH=. uv run sensors/bme680.py --node cabin          # fake publisher — pipeline test harness
```

Tooling recipes live with their subsystems: precache + terrain build → basemaps.md, gpsd/NTP setup + validation → hardware.md, orbit/pass backtests → observatory.md.

### Tests

Run with `uv run pytest`. `pytest` is a dev dependency (`[dependency-groups].dev`), kept out of the runtime/offline install path. `tests/` covers three surfaces: the load-bearing pure logic (geometry, timestamps, params, reader/protocol logic), the API read paths via a Flask client against a temp SQLite DB (`tests/conftest.py`), and the backtest tools' pure helpers. The directory is the inventory — keep new load-bearing logic and API reads covered.

### Linting & formatting

`uv run ruff check .` / `uv run ruff format .`. `ruff` is a dev dependency, same offline carve-out as pytest. Config lives in `[tool.ruff]` (pyproject.toml) — a deliberately lean rule set (real bugs + modern idioms, low noise); grow it there as needed.

### Type checking

`uv run mypy .` must be clean. `mypy` (+ `types-requests`) is a dev dependency, same offline carve-out as pytest/ruff. Config lives in `[tool.mypy]` (pyproject.toml) and runs **strict core / lenient rest**: a lenient global baseline (real errors in annotated code, untyped function bodies left unchecked) with a strict per-module override (`disallow_untyped_defs`/`disallow_any_generics`/…) on the load-bearing, well-typed core — `processor.*`, `common.*`, `logger.*`, `api.db`, `api.params`. Untyped libs (`obd`, `paho.mqtt`, `sgp4`) are `ignore_missing_imports`. Ratchet the strict surface outward over time: next `disallow_untyped_calls`/`disallow_untyped_decorators`, then widen the strict module list to `api.routes.*`/`tools.*`/`sensors.*`/`mqttbus.*` (the routes mainly need handler return types).

## Offline Constraint

All runtime dependencies must work without internet. Frontend libraries are npm deps that Vite **bundles into the committed `static/dist/`** (the bundle is offline; the Pi never builds — rebuild + commit before pushing); basemap *data* assets stay in `static/vendor/basemap/`. Python packages install from `uv.lock` at deploy time — no network needed after `uv sync`. The project itself is an editable-installed package (hatchling `[build-system]` in `pyproject.toml`, flat-layout packages enumerated there), so `uv sync` also *builds* it — the hatchling build backend must be in the Pi's uv cache for an offline deploy (cached automatically on the first online `uv sync`). That editable install is what lets any script (`uv run tools/foo.py`) import `common`/`api`/`processor` without a `sys.path` shim. The vector OSM basemap renders fully offline (bundled MapLibre/pmtiles + the local PMTiles archive); USGS raster renders from its on-disk cache, and the tile proxy only reaches upstream when online.

A few runtime deps are **system packages** that work offline once installed but aren't carried by `uv sync` — install them on the Pi while online (one-time): `nginx` for the web front door (+ its one-time bring-up: `rm /etc/nginx/sites-enabled/default`, the `deploy/gps-dashboard.nginx.conf` → conf.d symlink, a sudoers line for `nginx -t`/`reload`, and the hook's nginx branch), `libhamlib-utils` for the radio `rigctld` service, `alsa-utils` (`arecord`/`amixer`) for the `radio-recorder` service (usually preinstalled on Raspberry Pi OS — verify), `ffmpeg` + the MediaMTX static binary (`/mnt/nvme/mediamtx/`) for the radio live-listen stream, and the non-unit config files the deploy hook does **not** copy (it installs only `deploy/*.service`/`*.timer`): the udev rules (`99-gps-dongle.rules`, `99-icom-civ.rules`, `99-digirig.rules`, `99-obdlink.rules`) and `asound.conf` (→ `/etc/asound.conf`). This matches the project's reading of the offline constraint: it governs *runtime* off-grid correctness, not avoiding cacheable dev-time/system installs.

The **root-600 secret env files** in `/etc/default/` are the same shape — hand-maintained, hook-skipped, loaded by a unit's `EnvironmentFile`: `gps-dahua` (`GPS_DAHUA_PASSWORD`), `gps-victron`, and `gps-broadcast` (the `GPS_BROADCAST_*` cloud-hub stream secrets the Broadcast tab's `/api/broadcast/feeds` interpolates, plus `GPS_BROADCAST_CLOUD_URL`/`_CLOUD_AGENT_URL` — the WG-tunnel base URLs the cloud status/snapshot/log proxy reaches; never committed, since the repo is public; see `.claude/modules/broadcast.md` B3/B7). The cloud-hub **agent** (`broadcast/cloud_agent.py` + `broadcast/cloud_agent.service`) is an off-repo manual install on the vps, not the Pi — deliberately outside `deploy/` so the hook never installs it here.

Development happens with internet available. Building the vector PMTiles archive, pre-caching USGS tiles, and vendoring assets are intentional prep steps before going off-grid.
