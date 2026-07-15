# Data Update System Plan

**Status: SHAPED 2026-07-15 — all six core decisions resolved in session; one
flagged item (OSM vintage-sharing) to review before Phase 3.** Treat this doc
as the durable, living plan — check items off as they land, record decisions
inline.

## Context

Every offline data chunk (places sources, wiki cache, basemap/terrain
archives, raster cache, SATCAT) is today updated by a different hand-run
script, with ordering rules ("GNIS after OSM", "wiki after OSM"), staging
steps (laptop download → scp), and sanity checks living only in docs and
operator memory. The 2026-07-15 doc-validation pass surfaced the failure
modes: forgotten re-run invariants, silent dedupe no-ops on wrong ordering,
no sanity floor under destructive full-replaces.

Goal: **one centralized chunk-manager on the Pi** — a declarative registry of
every offline chunk, derived freshness ("am I ready to go dark?") surfaced in
a Systems drill-in, and a runner that executes updates Pi-side wherever the Pi
can act. Minimize external-infrastructure dependence: the van's WAN is
Starlink (300 GB/mo cap; the HaLow bridge is inter-site only, not the WAN
path) and a worst-case full refresh is ~50 GB/mo, so everything except the
static terrain archive can update directly on the Pi.

Relationship to other plans: `plans/navigation-plan.md` pins one PBF snapshot
on rex-nas so the Valhalla graph and the POI tier share an OSM vintage — see
the flagged item before letting the Pi pull its own PBFs.

## Chunk inventory

| Chunk | Size / transfer | Update path (target state) | Cadence |
|---|---|---|---|
| NPS places+events | ~100 MB API walk | Pi, WAN | ~monthly |
| RIDB places | 245 MB public zip | Pi downloads + imports | ~seasonal |
| GNIS names | 37 MB public zip | Pi downloads + imports (chained after OSM) | ~seasonal |
| OSM POIs | ~16 GB Geofabrik PBFs → ~3 GB transfer DB | Pi downloads + builds + merges (hours, detached) | ~seasonal |
| Wikipedia cache | ~2–3 GB API fetch (hours, resumable) | Pi fetches + merges | after OSM |
| Vector basemap | ~32 GB `pmtiles extract` of a dated Protomaps planet build | Pi extracts + atomic-replaces | ~seasonal/on demand |
| Terrain DEM | 98 GB — **static source dataset** | no update action; coverage-expansion recipe only | never |
| USGS raster cache | user-chosen (estimate shown) | Pi (`precache.py`) | on demand |
| SATCAT metadata | tiny | Pi fetch | ~weekly when online |
| Phone timeline | Takeout export | staged file → import | on demand |

Read-only status rows: drone sync (already timer-driven), docs vault
(git-push-driven).

## Confirmed decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Scope | **Every offline chunk**, one chunk-manager service | The dashboard question is "am I ready to go dark?" — only answerable if all chunks appear in one panel. New chunks become registry entries (the `sensor_schema.py` pattern). |
| 2 | State model | **Declarative registry + derived freshness; `update_runs` for history only** | Freshness computed at read time from the data itself (`max(synced_at)` per slice, file mtime/size, gap counts) — cannot drift when someone runs an importer over SSH. Ordering rules become derived comparisons (GNIS stale ⇐ osm synced_at > gnis synced_at; wiki gap = count of uncached wiki keys on OSM rows). A run row never feeds staleness. |
| 3 | Where updates run | **Pi-centralized; laptop/NAS recipes remain fallbacks** | Starlink cap 300 GB/mo vs ~50 GB worst-case full refresh; 722 GB free on NVMe. Basemap "build" is really a ranged download (`pmtiles extract`); the OSM build's heavy pyosmium pass runs on the ~1% prefiltered file. Terrain is static — excluded from updates. |
| 4 | Execution model | **Detached subprocess runner** (`python -m updater.run <chunk>`) | Survives deploy restarts (every push restarts gps-dashboard). Runner writes its own `update_runs` row + log file; Flask is a pure reader (status = derived freshness + runs + pid liveness; dead pid ⇒ failed). Single-flight global lock (sidecar WAL single-writer; POST returns 409 when busy). Cancel = SIGTERM; imports are transactional so cancel is safe. Same entry point is the CLI. |
| 5 | UI home | **Systems drill-in** (`/systems` card → `/data`) | Data freshness is system health; few-times-a-month surface doesn't earn a ninth tab. Same pattern as `/trends`/`/fridge`/`/gpsd`/`/ntp`. Later (not now): a "N chunks stale" line on the Home glance. |
| 6 | CLI shape | **In-process orchestration** — `updater/` imports the existing importer functions; download/staging code is new and lives in `updater/` | No stdout-parsing wrapper (rejected: shell-out), no 1,659-line move (rejected: full absorption). `tools/` CLIs keep working for SSH use; canonical path is `updater.run`; both execute the same functions. |

## Architecture

New top-level module **`updater/`** (peer of `processor/`; strict-mypy
surface, tested):

- `updater/chunks.py` — the `CHUNKS` registry: id, label, consuming section,
  freshness probe, staleness thresholds (reuse the 45/180-day tiers), expected
  transfer size (the UI shows cost before you press the button), action type
  (`pi_run` / `staged_file` / `recipe_only` / `readonly`), ordering deps.
- `updater/probes.py` — derived-freshness implementations (DB slice
  timestamps, file mtime+size, wiki-gap count, staged-file detection).
- `updater/run.py` — the runner: multi-step jobs (an update = ordered steps;
  the OSM chain is download PBFs → build transfer DB → merge → **re-run GNIS
  import** — the ordering invariant becomes code, not memory), `update_runs`
  bookkeeping, log capture, SIGTERM handling per the tools convention.
- `updater/fetch.py` — the new download layer: RIDB/GNIS/Geofabrik/SATCAT
  fetchers, `pmtiles extract` wrapper + atomic archive replace (extract to
  `.tmp`, verify, `mv` — the documented rsync pattern, done locally),
  staging-dir management (`/mnt/nvme/data/staging/`).
- `update_runs` table in the **main** DB (chunk, started, finished, status,
  pid, log path). Tiny, disposable history; not rebuildable but not worth
  backing up either.

API surface: `GET /api/data/status` (all chunks, derived freshness + in-flight
run), `POST /api/data/update/<chunk>` (validate → spawn detached → 202; 409
when busy), `GET /api/data/runs/<id>` (status + log tail), `POST
/api/data/runs/<id>/cancel`.

Sanity floor (from the validation pass): before any destructive full-replace,
compare incoming row counts per kind against the existing slice; abort below
threshold (parks 470→12 is the tell). Lands once in `import_places.load()`;
every caller — CLI or runner — inherits it. `--force` overrides.

One-time Pi system installs (join `libhamlib-utils` in the offline-carve-out
list in CLAUDE.md): `osmium-tool` (apt), `pmtiles` (ARM64 Go binary). The
`osmium` Python package is already a main dependency.

## Phases

- [x] **Phase 1 — see** (landed 2026-07-15): `updater/chunks.py` + probes,
  `GET /api/data/status`, read-only `/data` drill-in (chunk list, freshness,
  staleness badges, derived ordering warnings like "GNIS predates OSM slice").
  No runner. Landed deviations: the wiki-gap count (an OSM-slice scan, minutes
  on the Pi) became the `max(place_wiki.fetched_at)`-vs-OSM ordering signal
  backed by a new `idx_place_wiki_fetched` index — the exact gap count moves
  to the Phase 3 wiki job's preflight; non-places staleness tiers set at
  SATCAT 30 d, basemap 180 d, terrain/raster/phone/drone/docs informational.
- [ ] **Phase 2 — run (small)**: runner + `update_runs` + POST/cancel + log
  tail UI. Chunks: NPS, RIDB, GNIS, SATCAT (direct download + import), OSM
  merge + wiki merge from *staged* files, phone timeline from staged Takeout.
  Sanity floor in `load()`. Staging-dir detection.
- [ ] **Phase 3 — run (big)**: on-Pi OSM chain (Geofabrik download → build →
  merge → GNIS re-import), on-Pi wiki fetch (long resumable job), basemap
  `pmtiles extract` + atomic replace. System installs documented. Resolve the
  vintage flag first.
- [ ] **Phase 4 — polish**: raster precache integration (region/zoom picker +
  size estimate), Home-glance stale count, retire the per-script ops notes in
  `places.md` §Sync ops in favor of the registry (keep the recipes).

## Flagged (discuss before acting)

- **OSM vintage-sharing with the Valhalla graph** (`plans/navigation-plan.md`
  builds the NA graph on rex-nas from a pinned PBF snapshot shared with the
  POI build). Pi-pulled PBFs diverge from that pin. Registry records each
  chunk's source vintage regardless; decide at Phase 3 (or when the
  navigation plan executes) whether the graph build consumes the Pi's dated
  download or keeps its own pin.
- **GNIS zip reuse in the OSM chain**: re-download (37 MB, trivial) vs reuse
  the staged copy. Lean re-download; decide in Phase 3.

## Eliminated pathways

- **Sync-state table as the freshness source** — second copy of the truth;
  drifts the moment an importer runs outside the system.
- **Long-lived updater daemon / systemd transient units** — an always-idle
  service (or systemd bus plumbing for a `pmorgan`-user web app) for a
  few-times-a-month action; the detached runner gets the survival property
  with stdlib.
- **Shell-out orchestration of the existing tools** — a wrapper around the
  disjointness this plan exists to remove.
- **Print-reminder / abort-flag point fixes** in `run_osm`/`run_gnis`
  (gaps 1–2 of the validation pass) — superseded by derived ordering status
  and the chained OSM job.
- **On-Pi terrain rebuilds** — the source dataset is static; z13 was already
  rejected (~369 GB) and z12 is built. Coverage expansion stays a laptop/NAS
  recipe.
