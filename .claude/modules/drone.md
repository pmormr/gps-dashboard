# Drone Telemetry

A fourth data stream beyond GPS, sensors, and OBD/Victron: **aerial GPS tracks
from DJI drones**, pulled from footage the user already shoots and archives.
Fleet: **DJI Mini 5 Pro**, **Avata 2**, **DJI Neo**. Unlike the sensor platform
this is **batch/offline import** (offload-then-ingest), *not* an MQTT live stream
— it slots in as a `tools/` importer feeding a derived track tier the frontend
overlays, the same capture → derive → serve → render shape as the GPS denoise
tier and the observatory.

## Source — embedded protobuf telemetry

Every DJI clip carries an embedded **protobuf telemetry track** (MP4 stream #1,
handler `CAM meta`, schema `dvtm_<model>.proto`) — no `.srt` sidecars needed.
**ExifTool ≥13.x decodes it natively** for all three drones (`dvtm_Mini5Pro`,
`dvtm_AVATA2`, `dvtm_dji_neo`). The extraction core is one command:

```
exiftool -ee -n -api QuickTimeUTC=1 \
  -p '$GPSDateTime,$GPSLatitude,$GPSLongitude,$AbsoluteAltitude' clip.MP4
```

→ one line per metadata frame (~60 Hz; GPS effectively ~10 Hz after dedup), each
with **UTC time (ms), full-precision lat/lon degrees, AbsoluteAltitude (MSL
metres)**. ExifTool normalizes the *tag names* across models, so one command
shape serves all three, but **field availability differs per model** — the
importer runs a superset `-f` pass that tolerates gaps (e.g. Mini 5 Pro lacks
`RelativeAltitude` → `-` placeholder → null; rows where lat/lon is `-` are
skipped). `QuickTimeUTC=1` is load-bearing — without it timestamps come back in
camera-local time, not UTC.

## Importer — `tools/import_drone.py`

Discovery → extraction → thinning → load, idempotent on the
`(model_code, first_fix_utc)` natural key. Reads the module docstring for the
backend/sink matrix; the durable shape:

- **Discovery** scans **recursively by content**, not by DJI's DCIM
  layout/filename pattern — it extracts where a `dvtm` track exists and **skips
  non-DJI / re-encoded exports** that carry no telemetry. The store is mixed
  (all three drones), so the importer is model-driven, never Mini-5-assuming.
- **Two extraction backends:** `--source DIR` runs the host's own `exiftool`
  over a local directory (the laptop SD/drive path); `--ssh HOST --remote-dir
  DIR` runs ExifTool inside a transient container on the remote
  (`docker run --rm -v DIR:/data:ro <image>`) so the **video bytes stay local to
  the NAS** and only the telemetry text crosses the LAN. Clips fan out across
  `--jobs` workers (extraction is single-core CPU-bound per clip); DB writes are
  serialized (single SQLite writer).
- **Two write sinks:** a local SQLite write (default — the Pi home sync) or
  `--api URL`, which POSTs each thinned flight to a running dashboard (the
  off-grid laptop path: scan an attached card, ship flights to the Pi over the
  van LAN). Both dedup on the natural key, so the API sink is idempotent without
  `--incremental`; `--incremental` skips already-loaded media for the local sink.
- **Thinning** is Reumann–Witkam via the shared `processor/simplify.py` (same
  simplifier as the GPS processed tier). `KeyboardInterrupt` → `"\nInterrupted."`,
  exit 130 (tools/ convention).

## Home sync — `deploy/gps-drone-sync.{service,timer}`

A Pi-side systemd **timer** (fires when home on the van LAN) runs the importer's
`--ssh rex-nas` path: the Pi drives the NAS, ExifTool reads the video bytes
locally on the NAS in the pinned `exiftool:13x` container
(`deploy/exiftool.Dockerfile`, built once on the NAS), only telemetry text
returns, and the Pi parses/thins/writes its **own** DB. No persistent NAS
service, no mount. The timer is a **timer-driven oneshot** — the post-receive
hook installs the units and enables the timer on a `deploy/` change but never
restarts it.

## Data tier

Derived from the source media, fully rebuildable (re-run the importer). Schema in
`api/db.py` `init_db`; the column list lives in CLAUDE.md's Data Model.

- `drone_flights` — one row per clip. Natural key `(model_code, first_fix_utc)`:
  **no DJI model exposes a serial**, so model + first-fix time separates clips
  (distinct drones rarely share a millisecond). `media_path` is the canonical
  rex-nas path, **NULL on an SD-card/API import** so a later NAS scan backfills
  it (a 200 on `POST`).
- `drone_track_points` — the thinned track; `abs_alt` is MSL metres, canonical
  ms-UTC puts drone points on the **same time axis as `gps_points`** (GPS-joinable).

## API — `api/routes/drone.py`

- `GET /api/drone/flights?bbox=&start=&end=&points=` — flights whose bounds
  **overlap** the (all-optional) bbox/time filters, each with its thinned track
  embedded; `points=0` returns metadata only. The map-overlay read.
- `POST /api/drone/flights` — idempotent ingest (the `import_drone.py --api`
  path). Body carries identity + thinned points; the **server** derives time
  bounds / bbox / `n_points` and dedups on the natural key (**201** import /
  **200** skip|backfill).

## Render — `static/js/drone.js` + the 3D overlay

The **🚁 Drone panel** on `/` toggles the overlay. On first enable it fetches
*all* flights once (tiny dataset, independent of the time picker) and renders the
`drone-line` layer, colored per model (Mini 5 Pro / Avata 2 / Neo). Click a track
→ popup with model, time span, `abs_alt` range, media path.

Tracks **float at flight altitude** (`abs_alt × exaggeration`, sea level fixed)
via a generic three.js overlay, not a flat drape:

- `static/js/overlay3d.js` (global `Overlay3D`) is a **reusable** elevated-data
  overlay keyed by *group* — `setLines(groupId, lines, {color,width})` /
  `setExaggeration` / `clear` / `pick`. Drones are the first consumer
  (`'drone'`); van tracks (`gps_points.altitude`) and other columns drop in later
  as `setLines('van', …)`. Built generic on purpose; only the polyline primitive
  exists until a consumer needs more.
- It is a MapLibre **custom layer** (`type:'custom'`, `renderingMode:'3d'`)
  compositing a three.js scene into MapLibre's own camera/GL context — MapLibre
  stays the basemap/terrain/label/PMTiles engine, this just positions vertices
  via `MercatorCoordinate.fromLngLat([lon,lat], altMeters)`. `abs_alt` is MSL and
  the terrain DEM is MSL, so points float at the correct height for free.
- Lines are vendored three `Line2` fat-lines (`static/vendor/three/lines/`):
  `LineMaterial.linewidth` is screen-space px (`resolution` set each render).
  Picking is **in-overlay** (`Overlay3D.pick` projects vertices through the cached
  render matrix, nearest-segment screen test) — three.js raycasting is unreliable
  against the matrix-injected camera. The terrain-exaggeration slider max is 8.

## Decisions / traps

- **Natural key is `(model_code, first_fix_utc)`, not serial** — no DJI model
  exposes a serial in telemetry. (An earlier draft keyed on `(serial, start_utc)`;
  superseded before anything landed.)
- **RW thinning is horizontal-only** — a near-vertical climb collapses
  (a known altitude-loss limitation). Revisit 3D thinning now that
  altitude actually renders, if the loss becomes visible.
- **Terrain-exaggeration registration:** tracks float at `abs_alt × exaggeration`
  (per-line `scale.z`/`position.z`, mirrored in `pick`) so they stay registered
  with the DEM as it stretches — they only lined up at exaggeration 1.0 before.
- **No elevated-line support in vendored MapLibre** — `line-z-offset` /
  `line-elevation-reference` are confirmed absent from v5.24.0 (the latter is
  Mapbox-only), which is *why* elevation goes through the three.js custom layer.
- **`gps_status` is a red herring** — the telemetry field doesn't track fix
  validity the way the name implies; use lat/lon presence to gate rows.
- **ExifTool ≥13.x required** — earlier versions don't decode the `dvtm` tracks;
  the NAS container pins it.
