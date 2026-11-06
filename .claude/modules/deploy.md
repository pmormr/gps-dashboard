# Deployment

How code reaches the Pi and what happens when it lands: the bare-repo + post-receive hook
deploy, the per-unit restart matrix, the NVMe path inventory, and the nginx → waitress
production topology. The rules (push with `git push all main`, never commit on the Pi,
build + commit `static/dist/` first) stay in the root CLAUDE.md; this is the mechanics.

## Deploy hook (post-receive)

Two systemd services run on the Pi: `gps-logger` (writes GPS data) and `gps-dashboard` (serves the web app), plus the enabled-gated sensor/radio/media units. All are managed via a bare git repo with a post-receive hook.

The hook runs `uv sync` (which also builds the project as an editable install — see Offline Constraint in the root CLAUDE.md), then restarts services based on what changed. It always restarts `gps-dashboard` and (if enabled) `mqtt-ingest` and `gps-processor`; each enabled sensor reader (`sensor-obd`/`-victron`/`-pi`/`-openwrt`/`-dahua`/`-fridge`) restarts when `sensors/` or its own unit changed, `radio-control` when its unit changed, `radio-recorder` when `radio/` or its own unit changed, `mediamtx` when its unit or `deploy/mediamtx.yml` changed, `radio-stream` when its unit changed, and `nginx` (`nginx -t` + `systemctl reload`, fail-safe: a bad config keeps the running workers) when `deploy/gps-dashboard.nginx.conf` changed — these restart branches are per-unit blocks in the hook, so a brand-new service needs its block added on the Pi. When any `deploy/` file changed it reinstalls all unit files (glob) into `/etc/systemd/system/` and `daemon-reload`s — so editing a service's env var (e.g. `GPS_TERRAIN_PMTILES_PATH`) deploys on push with no manual `systemctl` step. `gps-logger` restarts only if `logger/` (or its unit) changed, to avoid GPS data gaps; `mosquitto` restarts only on its own config changes; `gps-drone-sync`, `gps-owntracks-sync`, `gps-db-backup`, and `weather-fetch` are timer-driven oneshots, not restarted (their timers are idempotently re-enabled on every push — `weather-fetch` re-enables only when already enabled, the sensor-reader `is-enabled` gate, to preserve its enabled-gating). The `pi` remote points to `pmorgan@192.168.42.178:/mnt/nvme/gps-dashboard.git`.

## Remotes, logs, status

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

## NVMe layout

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
- `/mnt/nvme/paul-network-docs.git` + `/mnt/nvme/paul-network-docs` — the network-docs vault, synced as its **own** bare repo + post-receive checkout (the same pattern as gps-dashboard, but a separate repo). Push from the local `../paul-network-docs` repo with `git push pi main`; the Docs tab reads the checkout via `GPS_NETWORK_DOCS_PATH` **and edits it** (saves auto-commit onto the bare repo's `main` via `GPS_NETWORK_DOCS_GIT_DIR` — the root CLAUDE.md's no-commits-on-the-Pi rule is gps-dashboard's, not this vault's; `git pull pi main` before pushing from the laptop). The repo's `.gitignore` keeps secrets/installers out of the sync.

## Production topology (nginx → waitress)

App runs at `https://van.pmormr.com/`, with plain `http://192.168.42.178/` deliberately un-redirected as the fallback (off-grid DNS loss, cert expiry on a long off-grid stretch). Production topology is **nginx (:443 + :80, LAN front door) → waitress (Flask app, `127.0.0.1:8000`)**. TLS is a hand-managed on-box conf (`/etc/nginx/conf.d/van-tls.conf` — **not** repo-deployed; certbot dns-cloudflare auto-renew needs internet, local DNS via the van router's dnsmasq; canonical doc: `plans/dns.md` in paul-network-docs): its :443 server and LAN-:80 listeners chain over loopback into the repo-managed server, which recovers the real client IP/scheme via realip + forwarded headers. nginx direct-serves `/static`, the two immutable PMTiles archives (`/tiles/osm.pmtiles`, `/tiles/terrain.pmtiles`), and the per-frame weather archives (`/tiles/weather/…` — a radar pan/zoom is a burst of hundreds of range reads that would starve waitress's thread pool) with native Range and owns the clean access log (→ syslog `/dev/log` → the pmpi1 relay → Graylog); it proxies WHEP signaling (`/whep/<path>`) to MediaMTX (:8889) so WebRTC live streams work from the https origin (the hub's own plain-HTTP port is mixed content there), and everything else (JSON API, SPA shell, raster tile proxy) to waitress. The app binds localhost:8000 (`GPS_BIND_HOST`/`GPS_BIND_PORT`); set `GPS_DEV=1` for the Werkzeug reloader/debugger during local iteration. nginx config is repo-managed (`deploy/gps-dashboard.nginx.conf`, symlinked into `/etc/nginx/conf.d/`, read from the checkout like `deploy/mediamtx.yml`).
