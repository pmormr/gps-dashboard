# Drive HUD plan — town compass + elevation records

**Status:** shaped 2026-11-07 (on the road); no code yet. Two independent
additions to the Drive view (`/drive`, frontend.md "Drive view"): a
Skyrim-style **town compass** (two bearing strips framing the map) and a
**records** block in the HUD (today's high/low elevation + a trailing
elevation sparkline). Both are display-only reads over data the Pi already
logs, plus one new places source.

## Decisions (locked unless new facts)

- **D1 — GeoNames is the population source.** The tier's only towns today are
  GNIS "Populated Place" rows (184k, no population, no city/town/village
  class), and the OSM build extracts no `place=*` nodes — the weighting has
  nothing to run on. GeoNames `cities500` (one ~10 MB zip, CC-BY 4.0, NA
  clipped to the basemap bbox) carries population + feature codes. Census
  Gazetteer rejected: US-only. Imported as `source='geonames'` through
  `tools/import_places.py` (new `--geonames-zip`) and registered as an
  updater `Chunk` (fetch + import + `places_slice` probe), same shape as GNIS,
  so it refreshes with the other sources from `/data`.
- **D2 — `population` is a real column on `places`**, NULL for every other
  source, with a partial index `WHERE population IS NOT NULL` (~100k rows).
  The nearby query filters/sorts on it; `json_extract` over `details` on an
  11.6M-row table has no index to lean on. Sidecar-only migration
  (`ALTER TABLE ADD COLUMN` in `_init_places_schema`).
- **D3 — Feature-code filter:** keep `PPL`, `PPLA`–`PPLA4`, `PPLC`, `PPLF`,
  `PPLG`, `PPLL`, `PPLS`; drop `PPLX` (neighbourhoods — would flood the strip
  inside any city), `PPLQ`/`PPLW`/`PPLH` (abandoned/destroyed/historical).
- **D4 — Map pins:** GeoNames rows land at rank 5 (search-only, never pinned)
  for v1 so the map doesn't grow a second town pin beside every GNIS one;
  the compass is the consumer. Follow-up (not v1): demote GNIS
  `populated_place` rows that match a GeoNames row by name within ~3 km, then
  promote GeoNames to the pinned community source. Search will show the
  duplicate pair until then — accepted.
- **D5 — Weighting = visibility radius + per-tier slots + hysteresis**
  (three mechanisms; one score can't satisfy "small towns nearby, cities
  from far, cities don't dominate"):
  - *Tier* = `floor(log10(population))` clamped to 1–5: 1 = <1k,
    2 = 1k–10k, 3 = 10k–100k, 4 = 100k–1M, 5 = ≥1M. One number drives the
    radius, the slot quota, the label weight, and the HUD pip glyph.
  - *Radius* `R(tier)` table (km), tunable: 1→8, 2→35, 3→110, 4→250, 5→400.
  - *Slots per strip*: tier 5+4 share 2, tier 3 gets 3, tiers 1–2 fill the
    rest (strip cap ~8), nearest-first within a tier.
  - *Hysteresis*: enter at `R`, leave at `1.15·R`; a mark stays on its strip
    until 10° past the ±90° fold line; membership changes debounce so a
    place at the slot boundary doesn't blink.
  - *Metro shadowing* (added 2026-11-07 after the first real-data read from
    Leadville): a candidate is hidden by any more-populous candidate within
    ~10° of bearing whose distance is in the same band (ratio 0.6–1.6).
    Denver's suburb ring (Lakewood/Arvada/Westminster/Thornton/Golden…, all
    tier 3–4 at ~60° / ~115 km) otherwise fills every slot *before* Denver,
    since nearest-first within a tier favours the suburbs. Boulder at 45°
    survives — it is a distinct direction.
- **D6 — A ring around the map, not strips** (revised 2026-11-07 on the
  dash: the two-strip version read as spread out, and the fold needed
  hysteresis). Each mark sits where the ray from the van at its relative
  bearing meets the map rectangle's edge (`ringPoint`): ahead = top edge,
  behind = the HUD's top edge, a town being passed slides down the side.
  No fold, no strip assignment, no snap; the only hysteresis left is
  membership (radius leave band + sticky seat). Labels place greedily in
  2-D: the edge spot, else one row inward, else dropped. No backing panel
  — text shadow over the map.
- **D7 — Population glyph + proximity size:** a fixed-width 5-cell pip bar
  (`▮▮▯▯▯`), cells = tier, beside the name and distance, plus label weight
  by tier. Fixed width keeps the label packer simple; the log bands are
  exactly the ×10 "star" idea with a 1k base (cities500's floor is 500).
  **Proximity** (added 2026-11-07): mark size + opacity scale 0.85×…1.25×
  with how deep the place sits inside its *own tier's* radius — normalised
  per tier, so a near village grows while a metro keeps a mid size from
  afar (raw distance would make cities permanently tiny).
- **D8 — "Today" = since the end of the last stop ≥ 4 h** (processor
  `track_points kind='stop'` dwell rows, the open/provisional dwell included),
  capped at 24 h back when no such stop exists. Not local midnight: the Pi's
  zone doesn't follow the van, and a late drive shouldn't split the day.
- **D9 — Elevation series is 1-minute mean buckets** over raw `gps_points`
  (`mode = 3` fixes only), since a max over raw 5 Hz altitude picks a ±10 m
  jitter spike. High/low come from the buckets with the time set; the last
  2 h slice feeds the existing `Sparkline` component.
- **D10 — Tap-to-navigate on a mark is deferred** until the navigation plan
  lands (`plans/navigation-plan.md`); marks are plain spans in v1 and the
  destination store is the one-line hook later.

## Work breakdown

1. [x] **GeoNames importer** — built 2026-11-07: `--geonames-zip` /
   `--geonames-admin1` in `tools/import_places.py` (D3 filter, bbox clip,
   `population` column + `idx_places_population`, one `populated_place` kind,
   summary "Village (county seat) · Colorado, US"); D2 migration
   (`_ensure_places_population`); `geonames` updater chunk (fetch + job +
   probe); `population` rides in every `/api/places` row. Real-data check on
   the laptop: 43,536 NA rows (36.7k village / 6.1k town / 727 city), bbox +
   population read plans onto the partial index. **Not yet run on the Pi** —
   push, then press the chunk's Update on `/data`.
2. [x] **`GET /api/places/nearby`** — built 2026-11-07: `lat`, `lon`,
   `radii` (five per-tier km, client-owned constants; defaults in
   `NEARBY_DEFAULT_RADII_KM`), `limit`. A plain per-tier distance filter —
   no ranking, nearest-first with `distance_m`/`bearing_deg`/`tier`;
   selection (slots, hysteresis, shadowing) is the client's. Helpers in
   `common/geo.py` (strict-typed). Leadville real-data read: 19 rows, 11 ms.
3. [x] **`web/src/lib/compass.ts`** — built 2026-11-07: radii + leave band,
   metro shadowing, per-strip slot quotas, sticky membership, fold dead
   zone, unrolled-ring `stripX`, 2-row greedy `packLabels` (priority: tier,
   then nearest the strip centre, then distance — a mark at the edge is
   about to change strips anyway), pips, compact miles. Vitest-covered.
4. [x] **Compass strips in `Drive.svelte`** — built 2026-11-07
   (`views/DriveCompass.svelte`): refetch on >2 km / 60 s, distance +
   bearing recomputed from the raw fix every selection (server values age
   with every metre), selection on a 3° heading step, per-frame positions
   off the smooth heading, behind strip pinned to the measured HUD height.
   Laptop check with a mocked fix near Leadville (playwright-cli route
   mock of `/api/gpsd/live` + the real GeoNames sidecar): Denver /
   Colorado Springs / Edwards / Breckenridge / Leadville North ahead,
   Leadville astern, labels inside a 393 px viewport. **Not yet seen on the
   dash** — tune R / slots / shadowing from the road.
5. [x] **`GET /api/points/elevation`** — built 2026-11-07: D8 window start
   (`DAY_BREAK_STOP_SECONDS`, open dwell included, 24 h cap) + D9 60 s mean
   buckets over 3D fixes, `high`/`low` with their times. Tested.
6. [x] **HUD records block** — built 2026-11-07: a `TODAY since hh:mm ·
   ▲ high · ▼ low` row above the main HUD row + a 2 h sparkline under ALT;
   `lib/records.ts` folds the live fix in (Vitest). Laptop check with a
   mocked profile renders; the "since" label ellipsizes on a 393 px phone.
7. [ ] **Road tuning + fold** — drive with it: R / slots / shadow angle,
   pip legibility, label density, the records row on the phone. The ring cap
   and the label edge inset became dash-side user settings 2026-11-08 (Drive
   HUD rail panel, `lib/drivePrefs.ts`) after the first days on the road —
   the remaining knobs stay constants until the road says otherwise. Then fold
   the durable bits (already drafted in `frontend.md` Drive view +
   `places.md` GeoNames) and drop this file. Pi steps after the push: the
   sidecar migration runs at service start; press **Update** on the
   `geonames` chunk in `/data` (needs WAN) — the compass is empty until then.

## Later (flagged, not scoped)

- More "records": today's top speed, miles today, steepest grade.
- GNIS↔GeoNames dedupe (D4 follow-up).
- Visited-before marks (Skyrim "discovered") from track history.
