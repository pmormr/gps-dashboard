# Attractions Tier

Parks/public-lands POIs + event schedules, batch-imported while online, browsed offline.
Same tier pattern as drone/phone: import → SQLite → bbox/date-filtered API → frontend.
The unified `attractions` table is the app's general POI substrate — OSM marks fold into
it eventually, and it's the anchor layer if a navigation feature ever lands.

Schema + API surface: CLAUDE.md (Data Model, API Endpoints). Frontend (map waypoints +
the Attractions destination): `.claude/modules/frontend.md`. This file keeps the source
facts, importer quirks, and sync ops.

## Sources

Two sources, one importer (`tools/import_attractions.py`), **full-replace per source**
(`source` column: `nps` | `ridb`) — an import never touches the other source's rows.

- **NPS API** (`developer.nps.gov/api/v1/`, api.data.gov key): parks, thingstodo, tours,
  visitorcenters, campgrounds, events. Whole-country dataset ≲100 MB JSON — sync it all,
  no regional scoping. Key resolution: `--api-key` → `NPS_API_KEY` env →
  `/etc/default/gps-attractions` (on the Pi, root:pmorgan 640). `DEMO_KEY` is 30 req/hr —
  spike-only, never the importer.
- **RIDB full CSV export** (`ridb.recreation.gov/downloads/RIDBFullExport_V1_CSV.zip`):
  public, no key, ~245 MB, regenerated ~nightly. Covers the other federal agencies
  (USFS, BLM, USACE, Reclamation…). Imported via `--ridb-zip <path>`.
- **State parks**: no unified source exists (that's the commercial apps' manual
  aggregation). Punted — federal + (later) OSM is the baseline.

## Importer facts (traps that cost time once)

NPS:
- `events` pages with `pagesize`/`pagenumber` (**1-based**; page 0 errors); the POI
  endpoints page `limit`/`start` (0-based). The events feed repeats ids → dedupe on GUID.
- `events` carry a pre-expanded `dates` array (concrete upcoming dates) alongside the
  RRULE — no RRULE parsing; the expanded dates index straight into
  `attraction_event_dates`. Summer recurrences run months out, so a ~monthly sync holds
  a season.
- Tour stops carry no coordinates — they reference `places` assets by id; the importer
  fetches `places` as a coordinate join and embeds lat/lon per stop. The tour pins at its
  first located stop, else its park. `thingstodo` sometimes lacks lat/lon → park fallback.

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
- Every payload carries `synced_at`; the UI wears data age (banner escalates past 45
  days) — schedule data degrades into "verify at the visitor center", never silently
  trusted.

## Deferred / parked

- OSM POI extract for non-federal coverage.
- Tour-audio + thumbnail caching for offline richness (tours are already fully readable
  offline via embedded transcripts).
- A "today at nearby parks" Home card; per-state parks data if coverage hurts.
- **Parked (discuss before acting):** store NPS `places` (~17k rows — waysides,
  monuments) as attraction rows; today they're only a tour-stop coordinate join.
