# Attractions POI Expansion Plan

## Context

Grow the attractions tier from federal-lands POIs (~22k NPS + RIDB rows) into a broad,
Google-Maps-like offline place database, and make the POIs the vector basemap already
*renders* also *searchable* in the Attractions view. Two data phases: an **OSM POI
extract** (the same data the basemap draws, with full tags) and **Overture Maps
Places** (the commercial layer — businesses, restaurants, chains — where OSM is thin).

Commercial APIs were evaluated and rejected: Google Places ToS prohibits storing
results (the entire point here is a persistent offline DB), and scraping Google/Yelp/
TripAdvisor is both a ToS violation and an arms race that doesn't fit one-shot bulk
imports. What no open source replaces: reviews, ratings, live hours/closures. Open
data covers everything else.

Treat this doc as the durable, living plan — check items off as they land, record
decisions inline.

---

## Confirmed decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Scope | **Everything POI-shaped, micro furniture included** | User call ("we can always filter it out or remove it; we have the space"). All POI-class keys — amenity, shop, tourism, leisure, historic, office, craft, healthcare, emergency, man_made (selective), destination natural features — *including* unnamed micro furniture (benches, bins, hydrants, drinking fountains). The only floor: mass-mapped non-places are excluded — individual trees (`natural=tree`), power poles/towers, street lamps, utility markers — which would roughly double the DB with unsearchable rows. Expected scale ~8–12M rows. |
| 2 | Coverage | **Full NA**, matching the basemap bbox (`-168,7,-52,72`) | User call. Searchable data everywhere the map renders. Geofabrik `north-america` + `central-america` extracts together cover the bbox. |
| 3 | Storage | **Sidecar DB** (`attractions.db`, `$GPS_ATTRACTIONS_DB_PATH`, `/mnt/nvme/data/attractions.db` on the Pi), ATTACHed by `get_connection()` | User call. Millions of rebuildable-from-public-download rows shouldn't inflate `gps_history.db` — and with it every 6-hourly `gps-db-backup` snapshot + rsync to rex-nas. |
| 4 | What moves to the sidecar | **The whole attractions tier** (`attractions`, `attraction_events`, `attraction_event_dates` — NPS/RIDB included) | One table, one query path — `/api/attractions` never UNIONs across DB files. Everything in the tier is full-replace rebuildable, so nothing in the sidecar needs backup. |
| 5 | Phase-1 source | **Geofabrik PBF extract, source-side** — not decoding the PMTiles archive | Tile data is render-optimized: attributes trimmed to the style's needs, geometry quantized, features thinned per zoom. The PBF is the same underlying data with full tags (`opening_hours`, `website`, `phone`, `cuisine`, …) → `details` JSON. |
| 6 | Phase-4 source | **Overture Maps Places** (GeoParquet on S3, DuckDB bbox extract, CDLA-Permissive-2.0) | The legal Google-analog: ~60M+ POIs (Meta/Microsoft-sourced; Foursquare's open 100M-POI set folded in 2025), with categories, addresses, confidence. Covers the commercial layer OSM is weak on. |
| 7 | Extract build placement | **Laptop/NAS builds, never the Pi** — extract → transfer DB → scp → Pi-side merge (`DELETE WHERE source='osm'` + `INSERT … SELECT` from the ATTACHed transfer file) | Same ops model as the tile archives for the heavy lifting, but a *merge* rather than whole-file replace, because NPS/RIDB syncs keep running on the Pi against the same sidecar. Full-replace-per-source semantics are preserved. |
| 8 | Search index | **FTS5** over name + category/brand/cuisine terms; keep `LIKE` only as an internal fallback | `name LIKE '%q%'` is a full scan — fine at 22k rows, not at millions. |
| 9 | Search ranking default | **FTS match quality → rank tier → distance to map center (current fix when no map context)** | The Google-like "nearest plausible match first" ordering. Set as the default now; tune weights during Phases 2–3 with real data. |

---

## Open decisions (execution-time — resolve in the named phase)

| # | Decision | Options | Notes |
|---|----------|---------|-------|
| B | Category taxonomy + rank tiers | How OSM tags (and later Overture categories) map to a unified `category` column + a `rank` INTEGER | `rank` gates map-pin zoom (national park at z5, gas at z10, café at z13, bench never…), mirroring how `labels.ts` tiers label density. NPS/RIDB rows get ranks too so one gate governs the overlay. Draft as a reviewable table in the Phase 1 tool; sanity-check against the dry-run stats. |
| C | Spatial index | Composite `(lat, lon)` index vs SQLite R*Tree | A lat-only-narrowing composite index may be fine; R*Tree is the right structure at ~10M rows *if* the Pi's SQLite ships with it (Debian's does — verify). Benchmark on real data in Phase 2 before committing. |
| E | Overture↔OSM dedupe | Overture rows carry source provenance (some are OSM-derived) | Prefer the OSM row when both exist (richer tags, matches the rendered map); Overture fills the gaps. Decide the matching key (provenance id vs name+proximity) in Phase 4. |

---

## Traps (identified up front)

1. **Nodes-only extraction silently drops ~half the POIs.** Shops/restaurants are
   frequently mapped on building *ways* (and some on relations). The extract must
   process ways + relations and compute centroids — `osmium tags-filter` keeps them,
   but the transform step needs geometry assembly (osmium export / pyosmium with a
   location index), not a bare node scan.
2. **FTS5 is token-prefix, not substring.** `q=creek` matches "Clear Creek Trail";
   `q=lear` no longer matches "Clear". Acceptable (Google works the same way) but the
   search UX contract changes — note it in the route docstring.
3. **ATTACH migration on the Pi.** The tier's tables exist in `gps_history.db` today
   (`api/db.py` `init_db`). Migration: create the sidecar schema, copy the ~22k rows,
   drop the old tables. Unqualified table names resolve across ATTACHed DBs only when
   the name is unambiguous — the drop must land in the same deploy as the ATTACH, or
   reads hit the stale main-DB tables.
4. **Viewport reads need a hard budget.** A z10 metro bbox can hold 100k+ broad-scope
   rows. The existing `limit` cap + rank gating must bound every map-overlay read;
   "all pins in view" is no longer a valid query shape below the rank gate.
5. **Pi merge is minutes, not seconds.** Bulk-inserting millions of rows + FTS rebuild
   on the Pi is a several-minute seasonal op (like the RIDB import). Run via SSH with
   the WAL busy-timeout in mind; never at drive time.
6. **Vintage drift between basemap and extract.** The PMTiles is a dated Protomaps
   planet build; the Geofabrik extract will be fresher. A searched POI may lack a
   rendered label or vice versa. Cosmetic — accept, don't chase.
7. **Transfer sizes.** Geofabrik NA PBF ~16 GB (build input, laptop/NAS only); at
   everything-scale the transfer DB is likely 3–8 GB. scp to the Pi at home over
   HaLow is slow — do it docked on the LAN, like the terrain archive.
8. **`gps-db-backup` must not grow.** The sidecar is intentionally *not* in the
   snapshot path (rebuildable from public downloads). Document the rebuild recipe in
   `tools/backup_db.py`'s docstring alongside the restore procedure, and in
   `.claude/modules/attractions.md`.

---

## Phases

### Phase 0 — Sidecar migration (no new data)

- [ ] `attractions.db` schema module + `GPS_ATTRACTIONS_DB_PATH` (Pi path + dev fallback, matching the tile-archive env pattern)
- [ ] `get_connection()` ATTACHes the sidecar; tier DDL moves out of `init_db`
- [ ] One-shot migration: copy existing rows, drop main-DB tier tables (same-deploy, trap 3)
- [ ] `import_attractions.py` + tests point at the sidecar; deploy notes (env var in unit files if needed)

### Phase 1 — OSM extract pipeline (laptop/NAS tool)

- [ ] `tools/build_osm_pois.py`: Geofabrik PBF(s) → tags-filter → centroid-resolved rows → transfer DB (`source='osm'`, natural key = element type + id)
- [ ] POI-key list + mass-non-place floor (decision 1) + `category`/`rank` mapping (open decision B) as data in the tool
- [ ] Full tags → `details` JSON; `summary` from name/category/brand
- [ ] Dry-run stats mode (counts per category) to sanity-check scope before building

### Phase 2 — Pi-side merge + query surface

- [ ] Merge mode in `import_attractions.py` (`--osm-db <transfer file>`): full-replace `source='osm'` from the ATTACHed transfer DB
- [ ] FTS5 table + rebuild step; spatial index decision (open C) benchmarked and applied
- [ ] `/api/attractions` grows: FTS-backed `q`, `category` filter, `rank` gate; every read bounded (trap 4)
- [ ] Route/param tests over a synthetic broad dataset

### Phase 3 — Frontend

- [ ] Attractions view: category browse chips + FTS search over the broad set; detail sheet renders OSM `details` (hours, phone, website, cuisine…)
- [ ] Map overlay: rank×zoom pin gating extending the existing viewport-driven sync (replaces the flat z6 gate for OSM kinds)
- [ ] Data-age banner semantics for `source='osm'` (seasonal cadence, not the 45-day NPS escalation)

### Phase 4 — Overture Places

- [ ] DuckDB bbox extract of the places theme → same transfer-DB shape (`source='overture'`)
- [ ] Category mapping into the Phase-1 taxonomy; dedupe vs OSM (open E)
- [ ] Same merge path; measure what it actually adds over OSM in a sample region before committing to full NA

### Phase 5 — Parked (discuss before acting)

- USGS GNIS (public-domain natural-feature names) if OSM gaps show up
- iOverlander (licensing/export state needs a fresh check post-iOverlander-2)
- Wikidata/Wikipedia enrichment join onto existing rows

---

## Constraints carried from the project

- **Offline-first.** All imports are online prep steps (dev laptop/NAS/docked Pi);
  the browse/search path reads only the local sidecar. No runtime WAN.
- **The Pi never builds.** PBF processing and Parquet queries happen off-Pi; the Pi
  only merges a finished transfer DB.
- **Full-replace per source.** `osm` and `overture` imports never touch `nps`/`ridb`
  rows, and vice versa.
- **Licensing.** OSM = ODbL, Overture places = CDLA-Permissive-2.0 — both fine for
  this use; keep source attribution in the detail sheet footer.
