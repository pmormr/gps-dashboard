# Places Tier

POIs + event schedules, batch-imported while online, browsed offline. Same tier
pattern as drone/phone: import → SQLite → bbox/date-filtered API → frontend. The
unified `places` table is the app's general POI substrate — federal lands (NPS/RIDB),
the broad ~10.8M-row OSM NA extract (basemap bbox `-168,7,-52,72`), and the GNIS
names layer in one table — and the anchor layer for navigation's destination search.
(Renamed from "attractions" 2026-07-09; the Pi secret file
`/etc/default/gps-attractions` keeps the historical name.)

Eliminated pathways (don't reopen without new facts): commercial APIs (Google Places
ToS forbids storing results; scraping rejected on ToS grounds), **Overture Maps**
(descoped 2026-07-10 — OSM coverage proved sufficient in real use; the
DuckDB-GeoParquet bbox extract stays the approach if commercial-layer gaps ever
show up), and **iOverlander** (its data now requires a paid license). State parks
have no unified source — federal + OSM is the baseline.

Schema + API surface: CLAUDE.md (Data Model, API Endpoints). Frontend (map waypoints +
the Places destination): `.claude/modules/frontend.md`. This file keeps the storage
model, source facts, importer quirks, and sync ops.

## Storage: the `places.db` sidecar

The whole tier (`places` + `place_events` + `place_event_dates`) lives in its own DB
file, ATTACHed as `places_db` by every `get_connection()` — millions of
rebuildable-from-public-download rows must not inflate `gps_history.db` or its 6-hourly
backup snapshot (the sidecar is deliberately **outside** the backup path; rebuild = re-run
the importers, recipe in `tools/backup_db.py`'s docstring).

- Path: `GPS_PLACES_DB_PATH` override, else **derived — `places.db` beside the main DB**
  (`/mnt/nvme/data/places.db` on the Pi, no unit-file env needed). Derived on purpose:
  every process runs `init_db`, so a unit with a divergent path could run the migration
  against the wrong file.
- Queries stay unqualified — the tables exist only in the sidecar, so names are
  unambiguous. **Invariant: no write transaction spans main + sidecar** (cross-file
  commits aren't crash-atomic in WAL); tier writes touch only `places_db`. (Reading an
  ATTACHed transfer file during a sidecar-write transaction is fine — writes only.)
- The sidecar is WAL like the main DB, so a minutes-long Pi-side merge never blocks
  dashboard reads, and tier writes never contend with the GPS logger.
- **Broad-POI columns + search index**: `places.category` (unified taxonomy) +
  `places.rank` (pin-zoom tier: 1 major destination ~z5+ … 5 micro furniture,
  search-only) govern every source through one gate — OSM rows arrive stamped by the
  build tool's TAXONOMY; NPS/RIDB kinds map via `api.db.PLACES_KIND_RANKS` at import.
  Client-side, the Map style panel's density slider *shifts* the rank×zoom gate
  (`maxRankForZoom(zoom, offset)`, web `places.ts`) — slider-left admits deeper ranks
  earlier; the shift is ignored below z6 (continent-bbox soup guard). The slider is the
  pin-density lever *by design*: the basemap's own marks max out at −1 (tile-data floor,
  basemaps.md).
  `places_fts` (FTS5, external content over name/summary/category/kind) backs `q=` with
  **token-prefix** semantics ('creek' matches "Clear Creek Trail"; mid-token substrings
  don't). The importer rebuilds it after every *places* import/merge (the wiki merge
  skips it — `place_wiki` isn't FTS content) — bulk-only writes, no sync
  triggers; the rebuild dominates merge runtime (~10.7M rows ≈ 5 min laptop-class).
  Spatial reads stay on the composite `(lat, lon)` index — R*Tree benchmarked
  indistinguishable at 10M rows on the bbox+rank+limit query shape and declined.
- **Query tuning at 10M-row scale**: partial indexes `idx_places_latlon_r{1,2,3}`
  serve the rank-gated viewport reads and `idx_places_rank_name` the no-bbox browse —
  the route emits index-friendly plain `ORDER BY rank` only when there's no bbox, so
  a sparse bbox can never plan onto the rank index and scan the whole tier (see
  `api/routes/places.py`). Search is **adaptive FTS**: count matches first, unbounded
  join ≤60k matches (full recall — a bbox'd search must see every match), top-10k bm25
  candidate pool above (junk prefixes degrade recall exactly where ranking millions of
  matches is meaningless). The 60k gate applies only to bbox'd searches: without a
  bbox the join itself is the cost, so an unscoped search goes bounded above 10k
  matches — pure relevance ranking, where the top-of-pool slice is the answer anyway. Two ops traps: benchmark new query *shapes* on the
  full-scale local DB (`~/osm-lab/places.db`) — the laptop absorbed a 45 s Pi-only
  sort once — and **pre-build new sidecar indexes over SSH before pushing**, or racing
  service startups all pay the build against busy_timeout.
- At full scale (NPS + RIDB + OSM NA + GNIS + wiki thumbs) the sidecar is ~11.6M
  rows / ~6.8 GB (Pi 2026-07-14: osm 10,791,484 · gnis 725,946 · nps 23,256 ·
  ridb 16,326 · 75,640 `place_wiki` rows). Cross-source twins that survive on
  purpose: ~2,090 NPS `site` rows name-match an OSM row within ~1 km — the NPS
  side is the richer twin, kept and **unified at display time**, not import-filtered.
- **Display-time twin unification** (`group_twins` in `api/routes/places.py`): every
  list read's returned page collapses exact-name (casefolded) ~1 km cross-source
  rows — nps > ridb > osm preference, the kept row carries `twins` refs (with
  `source_id`, so the client also suppresses a grouped-out OSM twin's basemap mark).
  GNIS never groups: its true twins are import-deduped; survivors sharing a name are
  *different* features (a town must not collapse into a shop). Same-source rows never
  group (two chain outlets are two places). **Page-local by design** — a nationwide
  exact-name search ('bear lake', 200+ exact matches) can cut a twin out of the page,
  but bbox'd browse/pin reads always co-locate twins, and the *detail* read finds
  them page-independently (box-first via the latlon index — never FTS-first, a
  generic name would score millions of matches). `GET /api/places/lookup` resolves a
  `(source, source_id)` natural key to the row id — the basemap tap-through bridge
  (tile feature id = planetiler `type·2⁴⁴ + osm_id`; codec in `web/src/lib/icons.ts`).

## Sources

Four sources, one importer (`tools/import_places.py`), **full-replace per source**
(`source` column: `nps` | `ridb` | `osm` | `gnis`) — an import never touches other
sources' rows. The `place_wiki` cache is a fifth, place-shaped-but-not-a-source slice
(below).

- **NPS API** (`developer.nps.gov/api/v1/`, api.data.gov key): parks, thingstodo, tours,
  visitorcenters, campgrounds, `places` assets (kind `site` — waysides, monuments,
  historic buildings), events. Whole-country dataset ≲100 MB JSON — sync it all,
  no regional scoping. Key resolution: `--api-key` → `NPS_API_KEY` env →
  `/etc/default/gps-attractions` (on the Pi, root:pmorgan 640). `DEMO_KEY` is 30 req/hr —
  spike-only, never the importer.
- **RIDB full CSV export** (`ridb.recreation.gov/downloads/RIDBFullExport_V1_CSV.zip`):
  public, no key, ~245 MB, regenerated ~nightly. Covers the other federal agencies
  (USFS, BLM, USACE, Reclamation…). Imported via `--ridb-zip <path>`.
- **OSM extract** (`--osm-db <transfer file>`): the broad ~10.7M-row POI layer —
  everything from fuel/campgrounds/peaks to benches, full NA (basemap bbox). Built
  **off-Pi** by `tools/build_osm_pois.py` (Geofabrik PBFs → `osmium tags-filter`
  prefilter → pyosmium nodes/ways/areas → transfer DB; the `TAXONOMY` table in that
  tool is the category/rank decision table, `REFINERS` the per-kind secondary-tag
  rules — aerodrome tiers, the named-lake gate). Ways/relations must keep geometry
  assembly (a nodes-only scan silently drops the ~half of POIs mapped on building
  ways). The Pi only ATTACHes the finished ~3 GB file
  and swaps the `osm` slice. Canonical PBF snapshot: `rex-nas:~/osm/` (shared with the
  navigation plan's Valhalla graph build so graph + POIs see one OSM vintage).
- **GNIS** (`--gnis-zip <path>`): the USGS Domestic Names national export (~37 MB zip,
  The National Map staged products, refreshed every other month, public domain) — the
  federal *names* layer: summits, lakes, streams, springs, valleys… plus populated
  places (the tier's `community` category; towns searchable by name). Class →
  category/rank table `GNIS_CLASS_RANKS` in the importer — **mouth-pinned linear
  classes (Stream/Valley/Canal/Channel/Gut/Arroyo) are rank 5 on purpose**: GNIS
  pins them at the mouth coordinate, so a map pin lies about where the feature
  is; they stay searchable by name. Two dedupe stages run against the live OSM
  slice: rows whose `feature_id` already rides in an OSM row's `gnis:feature_id`
  tag are skipped, then `outdoors` rows exact-name-matching a same-feature-category
  OSM row (`outdoors`/`attraction`/`park`) within ~1 km (`osm_name_dupes` — the
  untagged remainder of OSM's GNIS seeding, ~14.4k rows; towns never dedupe, the
  OSM slice has no settlement rows). **Re-run the GNIS import after every OSM
  merge** (a fresh OSM slice moves both dedupe boundaries).
  Pi-side import like RIDB (pipe-delimited parse, no geometry work).
- **State parks**: no unified source exists (that's the commercial apps' manual
  aggregation). Punted — federal + OSM is the baseline.

## Wikipedia cache (`place_wiki`)

Offline blurb + thumbnail for every wiki-tagged place. ~166k OSM rows carry a tag,
but ~85% of the wikidata-only QIDs have no English article — the real cache is
~75.6k articles (~2.2 GB, thumbnails dominating). Keyed by wiki id (`api.db.place_wiki_key`: wikidata QID, else
`lang:title`), **not** `places.id` — full-replace merges would orphan a places-keyed
cache; the detail read resolves place → key from its tags at read time and joins.
Built off-Pi by `tools/fetch_wikipedia.py` (resumable — fetched keys + misses persist
in the output DB; QID-only rows resolve to an article via the Wikidata API with
en → es → fr sitelink preference — Mexico/Québec places often have no enwiki),
merged with `--wiki-db`. Extracts are CC BY-SA 4.0 — the detail sheet
attributes; the thumbnail blob is served by `/api/places/<id>/photo`. Re-run the fetch
after OSM rebuilds to pick up newly tagged rows (existing keys are skipped, so
incremental runs are cheap).

## Importer facts (traps that cost time once)

NPS:
- `events` pages with `pagesize`/`pagenumber` (**1-based**; page 0 errors); the POI
  endpoints page `limit`/`start` (0-based). The events feed repeats ids → dedupe on GUID.
- `events` carry a pre-expanded `dates` array (concrete upcoming dates) alongside the
  RRULE — no RRULE parsing; the expanded dates index straight into
  `place_event_dates`. Summer recurrences run months out, so a ~monthly sync holds
  a season.
- Tour stops carry no coordinates — they reference NPS `places` assets by id (the NPS
  API's own asset type, unrelated to our `places` table); the importer fetches that
  endpoint as a coordinate join and embeds lat/lon per stop. The tour pins at its
  first located stop, else its park. `thingstodo` sometimes lacks lat/lon → park fallback.
- The assets also load as kind `site` rows (~17k): `isMapPinHidden`/coordless records
  demote to search-only rank 5 via `Place.rank_override` (never pin a spot the source
  hides or the park fallback invents), and assets name-shadowed by a same-park
  dedicated row (visitor center/campground/park) are skipped — those endpoints carry
  the richer structured detail.

RIDB:
- **The export has no operating-hours table** — descriptions/directions/fee text/phone
  are what exists.
- Kinds: `recarea` (park-analog container; joins `park` below the map's zoom gate) and
  `facility` (generic trailhead/cabin/boat-ramp type) are RIDB-new; `campground`/
  `visitorcenter` reuse the NPS kinds; `permit` = Permit + Timed Entry kept as planning
  signals. Excluded: pure reservation products (Activity Pass, Tree Permit, Ticket
  Facility, Venue Reservations), NPS-org rows (org 128 — the native source is richer),
  nameless junk.
- RIDB `park_code` = owning `RecAreaID` (numeric — the UI hides numeric park codes and
  shows `details.recAreaName`); facility coords fall back to the rec area's; blank/`0.0`
  coords are treated as absent (NULL-coord rows never match a bbox).
- Individual campsites don't become rows — they aggregate per campground into
  `details.campsites` (site count + per-equipment max vehicle length, ft — the
  "can my van fit" field).

## Sync ops

- **NPS** ~monthly, on the Pi over SSH while the van has WAN (usual at home via HaLow),
  so pre-expanded event dates stay ahead of the calendar.
- **RIDB** ~seasonally (facility data drifts slowly): download the zip on the laptop,
  `scp` to `/mnt/nvme/data/` on the Pi, run the importer there with `--ridb-zip`. The Pi
  never pulls the 245 MB itself.
- **OSM** ~seasonally: rebuild the transfer DB on the laptop/NAS from fresh Geofabrik
  extracts, `scp` to the Pi **docked on the LAN** (3 GB over HaLow hurts), run
  `--osm-db`. The merge + FTS rebuild is minutes-long — never at drive time.
- **GNIS** ~seasonally, chained **after** the OSM merge (the dedupe reads the fresh OSM
  slice): download the zip on the laptop, `scp`, run `--gnis-zip` on the Pi.
- **Wiki** ~seasonally, after OSM rebuilds: `tools/fetch_wikipedia.py` on the laptop
  (resumable; incremental re-runs only fetch new keys), `scp` the transfer DB, run
  `--wiki-db` on the Pi.
- Every payload carries `synced_at`; the UI wears data age (banner escalates past 45
  days for federal rows, 180 for the osm/gnis bulk sources) — schedule data degrades
  into "verify at the visitor center", never silently trusted.

## Deferred / parked

- Tour-audio caching for offline richness (tours are already fully readable
  offline via embedded transcripts).
- A "today at nearby parks" Home card; per-state parks data if coverage hurts.

Eliminated (2026-07-14 backlog wrap-up — don't reopen without new evidence):

- **Events server-side `q`** — the events corpus (NPS programs, hundreds–low
  thousands of rows) fits entirely inside the client's 2000-row fetch, so the
  client-side filter is complete. Revisit only if the corpus outgrows the fetch.
- **Places list virtualization** — search caps at 200 rows, browse at 2000;
  neither has hurt on-device. If browse ever does, slice incrementally
  ("show more"), don't add a virtualization lib.
- **`moveend` coalescing for the pins overlay** — `syncPlaces` aborts the
  in-flight fetch per moveend (correctness handled); coalescing would only save
  redundant warm bbox queries at tens of ms each.
