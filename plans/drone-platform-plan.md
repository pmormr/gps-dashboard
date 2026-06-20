# Drone Telemetry Platform Plan

## Context

A fourth data stream beyond GPS, sensors, and (planned) OBD/IMU: **aerial GPS
tracks from DJI drones**, pulled from the footage the user already shoots and
archives. Fleet: **DJI Mini 5 Pro** (+ high-end RC), **Avata 2**, **DJI Neo**.

The enabling discovery (2026-06-20): every DJI clip carries an embedded
**protobuf telemetry track** (MP4 stream #1, handler `CAM meta`, schema
`dvtm_Mini5Pro.proto`) — no `.srt` sidecars needed (captions were off). **ExifTool
13.55 decodes it natively** for all three drones (`dvtm_Mini5Pro`, `dvtm_AVATA2`,
`dvtm_dji_neo`). The whole extraction core is one validated command:

```
exiftool -ee -n -api QuickTimeUTC=1 \
  -p '$GPSDateTime,$GPSLatitude,$GPSLongitude,$AbsoluteAltitude' clip.MP4
```

→ one line per metadata frame (~60 Hz; GPS effectively ~10 Hz after dedup), each with
**UTC time (ms), full-precision lat/lon degrees, AbsoluteAltitude in metres**. ExifTool
normalizes the *tag names* across models, so the same command shape serves all three —
but **field availability differs per model** and only the Mini 5 Pro is validated
end-to-end so far (see decision 9 + Phase 0). The rex-nas store is **mixed: footage from
all three drones**, so the importer is model-driven, not Mini-5-Pro-assuming.

This is fundamentally a **batch/offline import** (offload-then-ingest), *not* a live
stream — so it is NOT the MQTT sensor pattern. It slots in as a `tools/` importer
(sibling to `precache.py`), feeding a derived track tier the frontend overlays — the
same shape as the GPS denoise tier.

Treat this doc as the durable, living plan — check items off as they land, record
decisions inline. Pairs with the deferred map redesign ([[next-ui-pass]]); sibling
streams: `obd-platform`, `motion-imu`.

---

## Confirmed decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Extraction engine | **ExifTool `-ee -p`** (shell out) | Off-the-shelf, maintained, decodes all three drones' `dvtm` protos + parses the per-frame UTC timestamp for free. Validated against a hand-rolled protobuf decode (lat/lon matched to the last digit). |
| 2 | Data tier | **`drone_flights` + `drone_track_points`** in `gps_history.db` | Mirrors the van's `annotations`+`track_points` model; **canonical ms-UTC** timestamps put drone points on the *same time axis* as GPS → time-correlation for free. Rebuildable from source media. |
| 3 | Idempotency / natural key | **`(drone serial, first-fix UTC)`** | Globally unique, stable across copies/renames. Lets the SD-now import and the NAS-later scan converge with no duplicate; the NAS scan **backfills the canonical media path** onto a flight first imported from the card. |
| 4 | Ingestion shape | **One portable extractor, two front-ends** | `tools/import_drone.py --source DIR` extracts locally, then either writes the DB (Pi-side) or `POST`s a Pi ingest API (remote). Both user scenarios fall out of one tool. |
| 5 | Home auto-sync host | **Pi mounts rex-nas** | Pi `systemd` timer mounts the NAS share when home, scans new clips, extracts over the mount, writes its own DB. No code deployed to the NAS; Pi owns the write. (Perf over the mount = Phase 0 validation.) |
| 6 | Manual / off-grid path | **Laptop CLI → Pi over van LAN** | Out boondocking, the laptop joins the van WiFi and reaches `192.168.42.178:5000` with no internet — consistent with the trusted-LAN/no-auth model. Scan an attached SD/drive, POST to the Pi. |
| 7 | v1 scope | **Data tier + sync + basic map overlay** | Land tables/CLI/API + a simple drone-track layer (altitude on the 3D terrain). Defer media pins / rich UI to the map redesign. |
| 8 | Media storage | **Blobs stay on disk (rex-nas); DB holds path + telemetry** | Same pattern as PMTiles/tiles on the NVMe — never ingest the ~10 GB videos into SQLite. |
| 9 | Multi-model store | **Self-identifying + content-based discovery + superset `-f` extraction** | rex-nas holds **Mini 5 Pro + Avata 2 + Neo footage mixed**. Each clip names its own `Protocol`/`Model`/`Serial` → store `model` per flight; the `(serial, first-fix UTC)` key separates drones inherently (distinct serials). Discovery scans **recursively by content** (not the Mini's DCIM layout/filename pattern), extracting where a `dvtm` track exists and **skipping non-DJI / re-encoded exports** with no telemetry. A single `exiftool -ee -f -p` superset pass tolerates per-model field gaps — *validated*: Mini 5 Pro lacks RelativeAltitude → `-` placeholder (parse as null); skip rows where lat/lon is `-`. **Field availability differs per model; only Mini 5 Pro is validated end-to-end** — Avata 2 / Neo pending real clips (Phase 0). |

---

## Open decisions (deferred — refine when we continue)

| # | Decision | Options | Notes |
|---|----------|---------|-------|
| A | **Relative altitude** | Enable DJI **Video Caption (SRT)** vs custom-parse the protobuf | AbsoluteAltitude (MSL) is the only altitude ExifTool maps for the Mini 5 Pro; **RelativeAltitude** (height above takeoff — the intuitive one for a map) is not. SRT `[rel_alt: …]` is the clean source (captions currently OFF). The protobuf field exists but my `.3.5.1` candidate was debunked. Avata 2 exposes rel-alt via ExifTool already. |
| B | **Flight grouping** | One row per clip (v1) vs group clips into flights by time gaps | DJI splits long flights across clips at a file-size cap. v1 = one `drone_flights` row per MP4 (simplest); grouping is a later refinement. |
| C | **Extraction engine, long term** | Keep shelling to ExifTool vs port the `dvtm` field map to pure-Python | ExifTool adds a Perl system dep (fine — import runs on Pi/laptop, never the offline-critical path). A pure-Python port matches the vendor-everything ethos; the only fiddly piece to replicate is the per-frame timestamp decode. Revisit only if the dep bites. |
| D | **Media-pin / catalog UI** | Clickable media pins, flights drawer, annotation correlation | Phase-2 UI; folds into [[next-ui-pass]]. |
| E | **Correlation depth** | Explicit `annotation_id` linkage vs shared-time-axis overlay only | v1 relies on the shared time axis (overlay by time window). Explicit "what did I shoot at this stop" linkage is a later join (drone flight ⟷ overlapping van annotation/position). |

---

## Constraints carried from the project

- **Offline-first.** Extraction runs at home (Pi+NAS) or on the laptop — never a hard
  requirement on the off-grid Pi's live path. The manual path needs only the **van LAN**
  (no internet). ExifTool/Python are dev/home tools, not new runtime deps for the dashboard.
- **GPS logging is sacred.** Untouched; the drone tier is read-derived and never writes raw.
- **Same SQLite DB.** New tables only; logger/processor never touch them. Remote writes go
  through the Flask API (serialized writer), avoiding multi-writer SQLite hazards; the
  Pi-side sync writes locally. WAL already in use.
- **Deploy model.** New Pi service (`gps-drone-sync` timer) slots into the bare-repo +
  post-receive hook; editing `deploy/` reinstalls units on push. **Never commit on the Pi.**
- **rex-nas is the canonical media store.** SD cards get wiped/reused; the NAS is the source
  of truth. "Home" = the Pi can reach rex-nas (mount succeeds).

---

## Architecture

```
 rex-nas  (canonical video store, ~75 GB/card)
    │  SMB/NFS mount, when home
    ▼
 ┌─ Pi: gps-drone-sync.timer ───────────────┐
 │  mount → scan new clips →                 │
 │  exiftool -ee -p → dedup(serial,first-UTC)│──── local write ─┐
 └───────────────────────────────────────────┘                  │
                                                                 ▼
 attached SD / drive on laptop (out & about)            SQLite gps_history.db
    │                                                    drone_flights
    ▼                                                    drone_track_points
 tools/import_drone.py --source DIR --api ──── POST /api/drone/flights ──┘
   (van LAN → 192.168.42.178:5000, no internet)     (same idempotent write fn)
                                                                 │
                                                                 ▼
                              frontend: drone-track layer on /  (abs_alt → 3D terrain;
                                                                  media path on click)
```

---

## Extraction method (validated)

- **Command:** the `exiftool -ee -p` one-liner above, run with **`-f`** and a **superset**
  format string (all altitude/attitude fields any model emits) so a model lacking a field
  yields a `-` placeholder rather than dropping the row. ~60 Hz frames; **dedup consecutive
  identical fixes** → ~10 Hz track (424 distinct positions in the 42 s test clip). Drop rows
  where lat/lon is `-`.
- **Model routing:** read `Protocol` / `Model` / `SerialNumber` (self-identifying, every
  clip) — `model` is stored per flight and feeds map styling; `Serial` is half the natural
  key. **Discovery is content-based**: recurse the store for any video, attempt extraction,
  skip files with no `dvtm` track (non-DJI footage, edited/re-encoded exports). Do **not**
  hardcode the Mini 5 Pro's `DCIM/DJI_001/DJI_<ts>_<idx>_D.MP4` layout — the Avata 2 and Neo
  name/organize differently.
- **Optional thinning:** reuse the processor's **Reumann–Witkam** simplification so drone
  tracks decimate like van tracks (importable from `processor/`).
- **Time base:** `GPSDateTime` is true UTC and matches the MP4 `creation_time`; the
  **filename time is local** (EDT in the sample) — never key off the filename. Parse
  `GPSDateTime` → `canonical_timestamp` ms.
- **Altitude:** `AbsoluteAltitude` in metres (raw protobuf mm ÷ 1000). Relative altitude
  pending decision A.
- **`gps_status=0` in the on-card index DBs is a red herring** — GPS is present regardless;
  do not gate on it. Those DBs (`Mini5Pro_edcf.db`, `FC8485.db`) are a file catalog only.
- **Per-model field sets:** Mini 5 Pro is sparse (GPS + AbsAlt + Temperature + frame#) and
  has **no RelativeAltitude** via ExifTool; Avata 2 is richer (**both** altitudes + drone
  attitude + ISO/shutter — rel-alt free); Neo is sparse like the Mini. The superset `-f` pass
  captures whatever each emits. **Only the Mini 5 Pro is validated end-to-end** — Avata 2 and
  Neo tag names/availability must be confirmed on real clips from rex-nas (Phase 0); they
  weren't reachable from the dev laptop (only Mini 5 Pro SD cards were mounted).

See memory `drone-telemetry-format` for the reverse-engineered field paths and gotchas.

---

## Phases

Sequenced so the data lands and is provable before any scheduling or UI — same incremental
tiered instinct as the denoise work. Phase 1 alone gets real tracks into the DB.

- **Phase 0 — De-risk (hard gate before building the importer).** (a) Time `exiftool -ee`
  against a 10 GB clip **over the rex-nas mount from the Pi** (metadata is a seekable MP4
  track — small bytes, many seeks — so likely fine, but confirm). (b) **Confirm Avata 2 +
  Neo** tag names / field availability on real clips from the mixed store (only the Mini 5
  Pro is proven). (c) Sanity-run **content-based discovery** across a mixed rex-nas folder —
  every drone's clips identified by `Protocol`/`Model`, non-DJI / re-encoded video skipped
  cleanly.
- **Phase 1 — Data tier + extraction core + local loader.** `drone_flights` /
  `drone_track_points` schema; `tools/import_drone.py --source DIR --db PATH` end-to-end on a
  local directory (the mounted SD card is the test fixture). Dedup on the natural key; thin
  to ~10 Hz.
- **Phase 2 — Pi home sync.** `gps-drone-sync.service` + `.timer`: mount rex-nas when
  reachable, incremental scan (skip already-imported by key), local DB write, path-backfill.
- **Phase 3 — Ingest API + remote CLI.** `POST /api/drone/flights` (idempotent, behind the
  same write fn) + `GET /api/drone/flights?bbox=&start=&end=`; `import_drone.py --api URL`
  for the laptop manual path over the van LAN.
- **Phase 4 — Basic map overlay.** Drone tracks styled distinctly from van tracks on `/`,
  **colored per drone model** (Mini 5 Pro / Avata 2 / Neo), `abs_alt` draped on the 3D
  terrain, media path surfaced on click. (Rich media-pin UI = deferred, folds into
  [[next-ui-pass]].)

---

## Codebase touchpoints (anticipated)

- **`api/db.py`** — `drone_flights` (serial, model, src/media path, start/end UTC, n_points,
  bbox) + `drone_track_points` (`flight_id`, canonical ms-UTC, lat, lon, abs_alt). Natural-key
  unique index `(serial, start_utc)`.
- **`api/routes/drone.py`** (new) — `POST /api/drone/flights` ingest + `GET` for the map
  layer (bbox/time filtered, size-aware like `/api/points`). Register the blueprint in
  `api/app.py`.
- **`tools/import_drone.py`** (new) — discovery (scan dir, dedup by key) + extraction
  (`exiftool -ee -p`, parse, thin) + loader (`--db` local / `--api` POST). `KeyboardInterrupt`
  → `"\nInterrupted."`, exit 130 (tools/ convention).
- **`deploy/gps-drone-sync.service` + `.timer`** (new) — Pi mounts rex-nas, runs the importer;
  hook installs the units on push.
- **`static/js/map.js`** — drone-track layer (distinct style; reuse the 3D/labels panels).
- **`processor/`** — expose the Reumann–Witkam simplifier for reuse by the importer.
- **`CLAUDE.md`** — add `/api/drone/*`, the new tables, and the service to the maps once landed.
```
