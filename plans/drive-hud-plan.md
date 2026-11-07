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
- **D6 — Strip geometry = an unrolled ring.** Top strip: relative bearing
  −90…+90, dead ahead at centre. Bottom strip: the back half, directly-behind
  at centre, left edge also −90 — a town passed on the left slides off the
  top-left edge and reappears at the bottom-left edge. Bottom strip shorter
  and dimmer, above the HUD. Driven from the live store's smoothed heading.
- **D7 — Population glyph:** a fixed-width 5-cell pip bar (`▮▮▯▯▯`), cells =
  tier, beside the name and distance, plus label weight by tier. Fixed width
  keeps the label packer simple; the log bands are exactly the ×10 "star"
  idea with a 1k base (cities500's floor is 500).
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
3. [ ] **`web/src/lib/compass.ts`** — pure: tier, radius, candidate set,
   hysteresis state, strip assignment, 1-D greedy label packing (≤2 rows per
   strip). Constants in one block like `follow.ts`. Vitest.
4. [ ] **Compass strips in `Drive.svelte`** — poll `nearby` when the van has
   moved >2 km (or 60 s), render marks + pip bars, re-layout per heading
   frame. On-device tune of R/slots from the road.
5. [ ] **`GET /api/points/elevation`** — D8 window start + D9 buckets;
   returns `{start, reason, buckets:[{t, alt}]}`. Tests.
6. [ ] **HUD records block** — ALT cell becomes current + ▲ high (time) +
   ▼ low + sparkline; live fix appended client-side; 30 s poll.
7. [ ] Fold durable bits into `frontend.md` / `places.md`, drop this file.

## Later (flagged, not scoped)

- More "records": today's top speed, miles today, steepest grade.
- GNIS↔GeoNames dedupe (D4 follow-up).
- Visited-before marks (Skyrim "discovered") from track history.
