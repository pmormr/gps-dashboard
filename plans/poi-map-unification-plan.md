# POI ↔ basemap-mark unification

Goal (user, 2026-07-14): make the basemap's own POI marks and the places tier
one system on the map — every visible mark browsable/tappable like a tier POI,
no double-rendering of the same feature, one icon language.

Status: **all six decisions locked with the user 2026-07-14 (bottom section);
execution not started.** The phasing below is the work plan.

## Fact base (verified 2026-07-14)

- **The id bridge exists.** Basemap `pois` features carry a planetiler feature
  id `type·2⁴⁴ + osm_id` (type 1 = node, 2 = way). Verified both directions
  against the tier: tile id `17598587138042` → `node/6401093626` ("The UPS
  Store"), `35185339696080` → `way/967607248` ("Denver Municipal Animal
  Shelter"). A tapped mark resolves to its tier row **exactly** — no fuzzy
  name/proximity matching needed.
- **Both systems are OSM; the tier is newer.** Basemap archive = planet build
  2026-03-28 (planetiler 0.10.2); the tier's OSM slice = Geofabrik 2026-07.
  "Importing the marks" is therefore a *selection* question (what the
  Protomaps profile shows vs what `TAXONOMY` keeps — 165 tag pairs over 14
  keys), not a data-availability question.
- **Basemap marks are icon+text symbols.** The `pois` style layer draws
  `icon-image: [kind]` from a 53-entry sprite + a name label. The shipped
  filter covers 36 kinds; `labels.ts` *replaces* that filter with its
  `POI_GROUPS` selection, several of whose kinds (`fuel`, `hospital`,
  `camp_site`, `hostel`, `pharmacy`…) have **no sprite entry → they render as
  bare text**. That's one visible inconsistency today, fixable independently.
- **Tile kinds are broader than POIs.** Sampled tiles carry landuse/boundary
  label points (`residential`, `industrial`, `retail`, `administrative`) and
  natural labels (`water`, `glacier`) alongside true POIs. Any "import the
  marks" pass must exclude the label-point classes deliberately.
- **Cross-source twins exist beyond GNIS.** Measured on the Pi 2026-07-14:
  2,090 of 16,320 located NPS `site` rows (~13%) have a same-name OSM row
  within ~1 km (266 vs GNIS) — e.g. "Bear Lake": the OSM lake and the NPS
  interpretive destination. Unlike GNIS (name-only stubs, dropped at import),
  the NPS row is the *richer* twin, so import-time skipping loses curated
  content. Left in place deliberately — resolving "which row represents a
  feature" belongs to this plan's identity story, not another import filter.
- **Tier pins are anonymous colored circles** (`place-circle` GL layer,
  per-kind color, no icon, no label); the emoji in `KIND_META`/`CATEGORY_META`
  are HTML-UI only (chips, legends, sheets). Search results get a halo+dot.
  Per-feature `min_zoom` in the tiles gives the basemap free density control
  at 10M scale, fully offline, with GL collision layout — the overlay
  re-derives a weaker version of this via the rank×zoom gate + bbox refetch.

## Design options

**Rendering backbone** (which system draws POIs):

- **A — tier renders everything, hide the basemap `pois` layer.** One system,
  trivially unified icons, everything tappable. Costs: continuous bbox API
  chatter while panning; re-implements density/collision the tiles already do;
  browse pins at city zoom become an API-shaped problem.
- **B (recommended) — the basemap renders ambient POIs; the tier resolves.**
  Marks become first-class via the id bridge (tap → decode → tier row →
  `PlaceSheet`; fallback mini-sheet from tile attrs for rows the tier lacks).
  The places overlay keeps its current role — search results, "show on map"
  waypoints, explicit kind-filtered browsing — and **suppresses basemap
  twins** while shown (a `['!', ['in', ['id'], …]]` filter on `pois` built by
  re-encoding the overlay rows' `source_id`s). Tiles keep doing density,
  collision, and offline rendering at scale.
- **C — zoom split** (basemap at low/mid zoom, overlay takes over up close):
  more moving parts than B and the handoff zoom is arbitrary; only worth it if
  B's suppression filter proves janky.

**Icon language:**

- **One shared sprite used by both layers** (recommended): vendor an
  open map-icon set (Maki/Temaki are the standard, CC0/BSD, SVG sources) and
  generate a committed sprite covering: every kind `labels.ts` surfaces, every
  tier pin kind (federal `site`/`campground`/… included), and the Protomaps
  defaults. The `pois` layer and a new symbol-based tier-pin layer (replacing
  the bare circles) reference the same images; text recolors per
  `CATEGORY_META`. A repo tool (`tools/build_sprite.py` or npm script)
  regenerates it so it stays maintainable offline.
- Alternative: adopt the existing Protomaps 53-icon sprite as-is for both
  (zero new assets, but it's missing van-relevant kinds — fuel, campground,
  hospital — and its style is fixed).

**"Import the OSM marks" (data side):** run a scripted census (tile sample ×
kind → `TAXONOMY` mapping) to produce the exact gap table: Protomaps kinds the
basemap renders that the tier's selection drops. Known candidates so far:
micro-amenities (`drinking_water`, `toilets`, `bench` — van-relevant?);
label-point classes to *keep excluded* (`residential`, `industrial`, `retail`,
`administrative`, bare `water`). Additions land in `TAXONOMY` + an OSM
re-import (the usual laptop transfer-DB → Pi merge → GNIS chain).

## Phasing (execution order)

0. **Kind census — DONE 2026-07-14** (26 tiles, 222 kinds, 5,156 ids checked
   against the tier): every *POI-shaped* kind resolves ~100% through the id
   bridge — including all POI_GROUPS kinds — so tap-through gets full detail
   for every mark visible today. The unresolvable mass is map furniture the
   style never surfaces (`tree`, `crossing`, `street_lamp`,
   `traffic_signals`, landuse/boundary label points, transit). **Decision 3
   was already satisfied**: `amenity=drinking_water` and `amenity=toilets`
   are in `TAXONOMY` (`utility`, rank 3) and in the tier — no re-import
   needed (phase 6 is a no-op; `bench` is `utility`/5, also already in).
   Sprite gap confirmed: 23 of the ~40 surfaced kinds have no icon (hotel,
   fuel, parking, hospital, camp_site, place_of_worship, bank…).
1. **Shared sprite** — vendor Maki/Temaki SVGs + a sprite build script;
   committed sprite covers every surfaced basemap kind (fixes today's
   text-only marks) + every tier pin kind (federal `site`/`campground`/…).
2. **Canvas unification** — restyle `pois` (new sprite icons + `CATEGORY_META`
   text colors); convert `place-circle`/search layers from circles to icon
   symbols on the same sprite; add the twin-suppression filter (re-encode
   overlay rows' `source_id`s → `['!', ['in', ['id'], …]]` on `pois`).
3. **Tap-through** — id decode + a tier lookup read (`source`+`source_id`
   param on `/api/places` or a tiny dedicated route) → `PlaceSheet`;
   unresolved marks open the minimal sheet from tile attrs (name/kind/coords,
   "not in the places index").
4. **One filter system** — merge the Labels panel's `POI_GROUPS` and the
   places layer's kind filter into a single POI control (they filter the same
   concept twice today).
5. **HTML UI icon swap** — `KIND_META`/`CATEGORY_META` emoji → the sprite SVGs
   inline (chips, legends, list rows, sheet headers).
6. **TAXONOMY additions** — `amenity=drinking_water` + `amenity=toilets`
   (+ any census surprises the user approves): transfer-DB rebuild on the
   laptop → Pi re-merge → GNIS chain, the usual recipe.
7. **Twin display-time unification** — search groups name+proximity twins and
   prefers the richer row (NPS > OSM > GNIS), pins suppress the duplicate,
   sheets cross-link the other source's row.

## Decisions (locked with the user 2026-07-14)

1. **Rendering backbone: basemap renders, tier resolves.** Tiles draw ambient
   POIs; taps resolve via the id bridge; the overlay keeps its
   search/waypoint/kind-browse role and suppresses basemap twins while shown.
2. **Icons: vendor Maki/Temaki** (CC0 SVG sources) + a dev-time sprite build
   step; one committed sprite is the icon language for both layers.
3. **Data gaps: import `drinking_water` and `toilets`; keep `bench` and
   transit (`bus_stop`/`platform`/`station`) excluded** — the tier stays
   destination-oriented plus van-practical amenities.
4. **Full icon unification** — the HTML UI (chips, legends, list rows, sheets)
   switches from emoji to the same sprite icons.
5. **Unresolved-tap policy: minimal sheet** from tile attrs — every mark
   responds; the sheet says the row isn't in the places index.
6. **Cross-source twins: display-time unification** — keep every row; search
   groups twins preferring the richer source, pins suppress duplicates, sheets
   cross-link. No import-time dropping beyond the existing GNIS stages. (A
   cached/materialized twin join is an acceptable implementation detail if
   read-time cost demands it.)
