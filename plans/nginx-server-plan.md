# Plan: Production web serving — nginx front + waitress app

**Status:** shaped, not started. Execute in a fresh session.
**Motivation:** get off the Werkzeug **development** server (`api/app.py:81`, `app.run()`),
which (a) prints "do not use in production", (b) colorizes its request log with ANSI
escapes that land as garbage in Graylog (`[35m`/`[1m`/`[0m` — the original trigger), and
(c) pushes the 33 GB/105 GB PMTiles archives through Python. Replace it with the standard
production topology: **nginx** as the front door + a real WSGI server (**waitress**) running
the Flask app on localhost.

> The ANSI log issue is resolved by this migration wholesale — removing the dev server. The
> earlier one-line `werkzeug.serving._log_add_style = False` fix was **proposed but never
> applied**; it's superseded here. Do not add it.

---

## Target topology

```
client → nginx (:80, LAN, default_server)
           ├─ /static/dist/assets/*   → disk, immutable-cached          (direct)
           ├─ /static/*               → disk                            (direct)
           ├─ = /tiles/osm.pmtiles     → /mnt/nvme/tiles/northamerica.pmtiles          (direct, native Range)
           ├─ = /tiles/terrain.pmtiles → /mnt/nvme/tiles/northamerica-terrain.pmtiles  (direct, native Range)
           ├─ access_log (full, clean, → syslog /dev/log → syslog-ng relay → Graylog)
           └─ /  (everything else: JSON API, raster tile proxy, SPA shell)
                                        → proxy_pass http://127.0.0.1:8000
                                                        └─ waitress → Flask app (create_app())
```

nginx cannot run Flask — it fronts a WSGI server. waitress runs the Python; nginx serves
static/large files + owns the access log. Two cooperating processes, standard pattern.

## Locked decisions
- **Front door = nginx on :80.** Users move from `http://192.168.42.178:5000` → `http://192.168.42.178/`.
- **App backend = waitress on `127.0.0.1:8000`** (localhost-only; nginx fronts it). Freed `:5000`.
  Backend ports 5000/8000 are fair game per the port grant; app uses 8000, 5000 goes unused.
- **waitress** (pure-Python, offline-safe, single-process thread pool) — preserves the app's
  existing threaded model (`db.py` opens SQLite `check_same_thread=False`; today's server is
  `threaded=True`), lowest RAM on the Pi, no fork semantics to reason about. Not gunicorn.
- **Access logging = FULL + clean**, owned by nginx (log everything incl. static, no ANSI,
  Graylog-parseable format). The app (waitress) emits no per-request access log.
- **nginx config is repo-managed** (`deploy/gps-dashboard.nginx.conf`), symlinked once into
  `/etc/nginx/conf.d/`, edits redeploy on `git push all` (hook does `nginx -t` + reload) —
  same read-from-checkout pattern as `deploy/mediamtx.yml`.
- **One-time bring-up only:** `apt install nginx` (OS package, can't ride `uv.lock` — same
  carve-out as hamlib/ffmpeg/mediamtx), the sites-enabled/default removal, the conf.d symlink,
  a sudoers line, and the Pi-side hook branch.
- **No nginx `try_files` raster-cache offload in v1** (proxy raster tiles to the app; the app
  already disk-caches them). Deferred optimization.

---

## Phase 1 — waitress app server (code)

1. **Add `waitress` runtime dep** to `[project.dependencies]` in `pyproject.toml`; `uv lock`;
   `uv sync`. Pure-Python (no build step); the wheel caches in the uv cache on this online
   sync so the offline Pi deploy works.
2. If `mypy` flags `import waitress` (no stubs), add `waitress` to the
   `[[tool.mypy.overrides]] ignore_missing_imports` list beside `obd`/`paho.mqtt`/`sgp4`.
3. **Rewrite the `__main__` block of `api/app.py`** (replaces line 81):
   ```python
   if __name__ == '__main__':
       app = create_app()
       host = os.environ.get('GPS_BIND_HOST', '127.0.0.1')
       port = int(os.environ.get('GPS_BIND_PORT', '8000'))
       if os.environ.get('GPS_DEV'):
           # Local iteration only: Werkzeug reloader + debugger. Never in production.
           app.run(host=host, port=port, debug=True, use_reloader=True)
       else:
           from waitress import serve
           serve(app, host=host, port=port,
                 threads=int(os.environ.get('GPS_THREADS', '8')), ident='gps-dashboard')
   ```
   Notes:
   - `create_app()` calls `get_connection()`/`init_db()` once at construction — fine in a
     single waitress process (no fork).
   - No ProxyFix / `trusted_proxy` needed: the app generates no external URLs and uses no
     client IP for auth (confirmed — no `url_for(_external)`/`redirect`). nginx logs the real
     client; `request.remote_addr` being `127.0.0.1` in-app is harmless.
   - `deploy/gps-dashboard.service` ExecStart stays `.venv/bin/python api/app.py` — the
     host/port move lives entirely in these defaults. (Optional: add a clarifying comment to
     the unit that the app now binds localhost:8000 behind nginx.)

## Phase 2 — nginx config (repo)

Create **`deploy/gps-dashboard.nginx.conf`** (dropped into `http{}` via the conf.d symlink):

```nginx
# Fronts the Flask app (waitress @ 127.0.0.1:8000). Symlinked once into
# /etc/nginx/conf.d/gps-dashboard.conf; edits redeploy on `git push all`
# (post-receive: nginx -t + reload). Serves the SPA bundle + the two immutable
# PMTiles archives straight from disk; proxies everything else to the app.
log_format gpsaccess '$remote_addr $status $request_method "$request_uri" '
                     'rt=${request_time}s uht="$upstream_response_time" '
                     'bytes=$body_bytes_sent ua="$http_user_agent"';

server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    access_log syslog:server=unix:/dev/log,tag=nginx,severity=info gpsaccess;
    error_log  syslog:server=unix:/dev/log,tag=nginx,severity=error;

    client_max_body_size 64m;          # docs PUT + drone LAN ingest
    gzip on;
    gzip_types application/json application/javascript text/css image/svg+xml;
    gzip_min_length 1024;

    # Content-hashed SPA assets → cache hard.
    location /static/dist/assets/ {
        alias /mnt/nvme/gps-dashboard/static/dist/assets/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
    location /static/ {
        alias /mnt/nvme/gps-dashboard/static/;
    }

    # Immutable basemap archives — nginx serves Range natively (offloads Python).
    location = /tiles/osm.pmtiles     { alias /mnt/nvme/tiles/northamerica.pmtiles; }
    location = /tiles/terrain.pmtiles { alias /mnt/nvme/tiles/northamerica-terrain.pmtiles; }

    # Everything else → the Flask app (JSON API, SPA shell, raster tile proxy/cache).
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;       # raster proxy reaching upstream when online
    }
}
```
- octet-stream PMTiles are deliberately **excluded** from `gzip_types` (already compressed).
- `location =` exact matches beat the `/` prefix; `/static/dist/assets/` (longer prefix) beats
  `/static/`. Precedence is correct as written.

## Phase 3 — Pi one-time bring-up (documented, run once online)

```sh
sudo apt update && sudo apt install -y nginx
sudo rm -f /etc/nginx/sites-enabled/default              # TRAP: kills duplicate default_server
sudo ln -sf /mnt/nvme/gps-dashboard/deploy/gps-dashboard.nginx.conf \
            /etc/nginx/conf.d/gps-dashboard.conf
sudo nginx -t && sudo systemctl enable --now nginx
```
- **sudoers:** grant the deploy user passwordless `/usr/sbin/nginx -t` and
  `/bin/systemctl reload nginx` — extend the existing sudoers the hook already uses for unit
  installs + `daemon-reload` + `systemctl restart`.
- Confirm nothing else holds `:80` first (verified free at plan time).

## Phase 4 — deploy hook branch (Pi-side, one-time)

Add a per-file block to `/mnt/nvme/gps-dashboard.git/hooks/post-receive` (reuse the hook's
existing changed-files detection — match its variable/mechanism, don't invent one):
```sh
# nginx config changed → validate and hot-reload (fail-safe: bad config keeps old workers)
if <changed-files contains deploy/gps-dashboard.nginx.conf>; then
    if sudo nginx -t; then sudo systemctl reload nginx && echo "  nginx reloaded";
    else echo "  !! nginx -t failed — keeping current config"; fi
fi
```
The symlink means the working-tree file (freshly `git checkout`'d) IS what `nginx -t` reads —
no copy step, mirroring the `mediamtx.yml` read-from-checkout model.

## Phase 5 — port/URL cleanup + docs (the `:5000` audit)

Update the **real** references (ignore the unrelated `5000` poll-interval/limit/test hits):
- `web/vite.config.ts:7` — dev proxy default `http://localhost:5000` → `http://localhost:8000`;
  update the AirPlay comment (8000 avoids the macOS `:5000` conflict). **Not bundled → no dist rebuild.**
- `tools/import_drone.py` — `--api` example/docstring `http://192.168.42.178:5000` → drop the
  port (`http://192.168.42.178`). **Check for and update any hardcoded `--api` default.**
- `CLAUDE.md:55` — "App runs at `http://192.168.42.178:5000`" → `http://192.168.42.178/`; and
  document the topology (nginx :80 → waitress :8000), the direct-served paths, nginx in the
  Offline-Constraint one-time-system-installs list, the new `deploy/gps-dashboard.nginx.conf`
  in the deploy/ + project-structure listings, and the hook branch.
- `.claude/modules/frontend.md:23` — local-dev note now `:8000` via `VITE_API_TARGET`.
- `web/src/lib/wakelock.ts:3` — comment `http://<LAN-IP>:5000` is stale (port only). **NB: port
  80 is still plain HTTP → still not a secure context → Wake Lock still won't work over LAN IP;
  no behavior change.** Comment-only; if you touch it, rebuild+commit `static/dist/` (stale-dist
  trap). Prefer to skip or batch to avoid a rebuild for a server migration.
- **Audit `deploy/*.service` + any tool that calls the dashboard by URL** (e.g.
  `gps-drone-sync.service` → what `--api` does it pass?) for `:5000` and move to port 80.
- Update the `local-dev-tile-archives` memory ("never port 5000 / AirPlay") — local app now on 8000.

## Phase 6 — verify + deploy

Local first: `uv sync` · `uv run python -m api.app` (serves 127.0.0.1:8000; map renders from the
local dev tile archives; API works — Flask still serves static/pmtiles locally without nginx) ·
`uv run pytest` (API tests use the Flask test client — unaffected) · `uv run ruff check .` ·
`uv run ruff format .` · `uv run mypy .`.

Then Phase-3 bring-up on the Pi, then `git push all` (hook: `uv sync` installs waitress →
restart gps-dashboard on :8000 → reload nginx). **On-device verify:**
- `curl -sI http://192.168.42.178/` → 200
- `curl -sI -H 'Range: bytes=0-99' http://192.168.42.178/tiles/osm.pmtiles` → **206**,
  `Server: nginx`, `Accept-Ranges: bytes` (proves nginx direct-serves the archive, not proxied)
- `curl -sI http://192.168.42.178/api/status` → 200 (proxied to waitress)
- `curl -sI http://192.168.42.178/static/dist/assets/<hashed>.css` → `Cache-Control: public, immutable`
- Pi `ss -tlnp` → nginx on `:80`, python/waitress on `127.0.0.1:8000`, **nothing on `0.0.0.0:5000`**
- Browser (playwright over the HaLow bridge): map renders offline, tiles load, tabs work
- **Graylog:** nginx access lines appear clean; the werkzeug `\x1b[` ANSI lines are **gone**
  (query `source:pmpi1 AND message:"["` or `[35m` → trends to 0). See the
  `graylog-access` memory for the auth recipe.

---

## Traps / risks
1. **Debian default site** — `rm /etc/nginx/sites-enabled/default` or `nginx -t` fails on
   duplicate `default_server`.
2. **`/dev/log` → Graylog** — the one real unknown: confirm nginx's syslog lines actually reach
   Graylog. On systemd, `/dev/log` is journald's socket and the pmpi1 syslog-ng relay drains
   journald → Graylog (how every current service gets there). Expected to work; **verify in
   Phase 6**. Fallback if not: `access_log /var/log/nginx/access.log` + a syslog-ng `file()`
   source, or stdout→journald.
3. **Privileged :80** — fine: the Debian `nginx.service` master runs as root and binds 80;
   the app stays unprivileged on 8000. No `cap_net_bind_service` needed.
4. **Offline dep** — `waitress` must be in the uv cache before going off-grid (this plan's
   online `uv sync` handles it; pure-Python, no build).
5. **API-client tools** — anything POSTing to the dashboard by URL breaks if it still says
   `:5000` (Phase 5 audit). 
6. **Local dev** — app now `127.0.0.1:8000`; set `GPS_BIND_HOST=0.0.0.0` only if you want the
   dev instance reachable from other LAN hosts.

## Rollback
Small and clean: revert `api/app.py` to the old `app.run(host='0.0.0.0', port=5000, ...)` line
(or run with `GPS_BIND_HOST=0.0.0.0 GPS_BIND_PORT=5000 GPS_DEV=` … ), `sudo systemctl stop nginx`,
push. Users hit `:5000` again. nginx is additive; the app change is a one-liner.

## Deferred (not v1)
- nginx `try_files` raster-tile cache offload (serve cached PNGs from disk, miss → app).
- TLS / off-LAN access (would make Wake Lock etc. secure-context-eligible).
- app → nginx over a unix socket instead of `127.0.0.1:8000`.
