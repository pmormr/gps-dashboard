# Attractions Panel — Plan

Nearby-activities layer for parks/public lands: self-guided tours, event schedules,
facility hours, campgrounds, things to do. Whole-country scope. Same tier pattern as
drone/phone: batch-import while online → SQLite → bbox/time-filtered API → map layer + panel.

Long-term direction (decided 2026-07-03): the unified `attractions` table is the app's
general POI substrate — OSM marks fold into it eventually, and it's the anchor layer if a
navigation feature ever lands. Priorities: self-guided tours, event schedules, facility hours.

## Phase 0 — source spike (DONE 2026-07-03)

Findings that shape everything below:

- **NPS API** (`developer.nps.gov/api/v1/`, api.data.gov key): the primary source.
  Whole-country totals: 474 parks · 3,563 thingstodo · 711 tours · 3,406 events ·
  ~600 visitorcenters · ~650 campgrounds. Entire dataset ≲100 MB of JSON — sync it all,
  no regional scoping needed.
  - `events` carry a **pre-expanded `dates` array** (concrete upcoming dates) alongside the
    RRULE + `times` + lat/lon + free/reservation flags. Summer recurrences run months out,
    so a ~monthly sync holds a season. No RRULE parsing needed — index the expanded dates.
  - `tours` are the self-guided kind: ordered `stops` with significance text, embedded
    **audio transcripts**, and directions-to-next-stop. Fully readable offline without
    caching the audio files.
  - `visitorcenters`/`campgrounds` have structured `operatingHours` (with exceptions),
    amenities, coordinates. `thingstodo` sometimes lacks lat/lon → fall back to the park's.
  - `DEMO_KEY` works but is 30 req/hr — fine for spikes, not the importer. **Needs a free
    NPS API key** (instant email signup) in the importer's env.
- **RIDB full export** (`ridb.recreation.gov/downloads/RIDBFullExport_V1_CSV.zip`): public,
  **no key needed**, 245 MB zip, regenerated ~nightly. Covers 12 agencies (USFS, BLM, USACE,
  Reclamation…) — facilities, campsites, tours, permits, hours, coordinates. Phase 3 source.
- **State parks**: no unified source exists (that's the commercial apps' manual aggregation).
  Punted — federal + (later) OSM POIs is the baseline. Revisit per-state open data only if
  coverage hurts in practice.

## Data model (rebuildable from source; full-replace per source on import, like the phone tier)

- `attractions(id, source, source_kind, source_id, park_code, name, lat, lon, summary,
  details_json, synced_at)` — one queryable row per POI (park, tour, thingstodo,
  visitorcenter, campground, later RIDB facility). Structure that only display needs
  (tour stops, hours text, amenities, fees, image/audio URLs) rides in `details_json`;
  columns exist only for what queries filter/sort on. Natural key `(source, source_id)`.
- `attraction_events(id, source_id, park_code, name, lat, lon, location_text, time_text,
  is_free, needs_reservation, details_json, synced_at)` +
  `attraction_event_dates(event_id, date, time_start, time_end)` — the expanded `dates`
  array as indexed rows, so "what's on this week near me" is one range query.

## API

- `GET /api/attractions?bbox=&kind=&limit=` — map/panel read; nearby ranking is client-side
  against the live fix (the frontend already holds it).
- `GET /api/attractions/<id>` — full `details_json` for the detail sheet.
- `GET /api/attractions/events?start=&end=&bbox=` — date-windowed events via the dates table.
- Every payload carries `synced_at` — the UI must wear data age prominently ("as of Jun 20");
  schedule data degrades into "verify at the visitor center", never silently trusted.

## Sync path

`tools/import_attractions.py` — run **on the Pi over SSH while the van has WAN** (usual state
at home via HaLow), writing straight into the live DB; `--db` for local dev. No ingest API
unless a laptop-push path proves necessary later. Key via `NPS_API_KEY` env.

## Phases

1. **NPS tier** — DONE 2026-07-03 (schema in `api.db`, `tools/import_attractions.py`,
   `api/routes/attractions.py`, tests). Implementation facts:
   - `events` pages with `pagesize`/`pagenumber` (**1-based**; page 0 errors); the POI
     endpoints page `limit`/`start` (0-based). The events feed repeats ids → dedupe on GUID.
   - Tour stops carry no coordinates — they reference `places` assets by id, so the importer
     fetches `places` as a coordinate join and embeds lat/lon per stop; the tour pins at its
     first located stop, else its park. **Open item:** store places as attraction rows too
     (they're real POIs — waysides, monuments), discuss before acting.
   - Key: `NPS_API_KEY` via `--api-key` → env → `/etc/default/gps-attractions` (in place on
     the Pi, root:pmorgan 640).
2. **Frontend** — DONE 2026-07-03 (`web/src/lib/attractions.ts` controller,
   `NearbyPanel.svelte`, `AttractionSheet.svelte`; live import verified on the Pi the same
   day). Implementation facts:
   - The map layer is **viewport-driven, not time-windowed** (moveend refetch, stale-token
     guard) with a zoom gate: below z6 only parks render. Kind colors/icons live in
     `KIND_META` (attractions.ts); pins are one domain-free circle layer in map.ts.
   - 🧭 Nearby rail panel: one bbox fetch (~±0.5°) around the live fix (map-center
     fallback), client-side haversine sort, top 50. Pin clicks and Nearby rows share the
     detail sheet; the always-on age banner escalates to a warning past 45 days.
   - Still a rail panel, not a nav destination — promote only if it earns it.
3. **RIDB facilities** — parse the full export (facilities + hours + activities, not the
   100k individual campsites) into the same `attractions` table, `source='ridb'`.
4. **Deferred / optional** — OSM POI extract for non-federal coverage; tour-audio + thumbnail
   caching for offline richness; a "today at nearby parks" Home card; per-state parks data.
