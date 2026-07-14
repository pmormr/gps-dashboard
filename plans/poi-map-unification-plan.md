# POI ↔ basemap-mark unification (prep — decisions pending)

Goal (user, 2026-07-14): make the basemap's own POI marks and the places tier
one system on the map — every visible mark browsable/tappable like a tier POI,
no double-rendering of the same feature, one icon language.

Status: **fact-finding done, design options drafted, blocked on the decision
questions at the bottom.** No code yet.

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

## Phasing sketch (pending decisions)

0. **Kind census** — script the gap table over a large tile sample; decide
   inclusions with the user.
1. **Quick fix (independent):** sprite entries for the `POI_GROUPS` kinds that
   render text-only today.
2. **Tap-through:** id decode + a tier lookup read (`source`+`source_id`
   param on `/api/places` or a tiny dedicated route) + `PlaceSheet` fallback
   for unresolved marks.
3. **Shared sprite + symbol pins:** generate/commit the sprite, restyle
   `pois` text to category colors, convert `place-circle`/search layers to
   icon symbols, add the twin-suppression filter.
4. **One filter system:** merge the Labels panel's `POI_GROUPS` and the places
   layer's kind filter into a single POI control (they filter the same
   concept twice today).
5. **TAXONOMY additions + re-import** if the census says so.

## Open questions (answer before code)

1. Rendering backbone: agree with **B** (basemap renders, tier resolves,
   overlay suppresses twins), or do you want the tier to render everything (A)?
2. Icon set: OK to vendor Maki/Temaki SVGs + a sprite build step (new vendored
   asset + dev-time tool, offline-clean), or hand-roll a minimal set?
3. Data gaps: which currently-excluded classes should import? Specifically
   micro-amenities — `drinking_water`, `toilets`, `bench`, transit
   (`bus_stop`, `platform`) — versus keeping the tier destination-oriented.
4. Do the HTML UI chips/legends/sheets switch from emoji to the same sprite
   icons (full unification), or does emoji stay the UI language and only the
   map canvas unifies?
5. Tap on an unresolved mark (tier lacks the row — vintage skew or excluded
   kind): minimal sheet from tile attrs (name/kind/coords, no detail), or
   nothing?
6. Cross-source twins (the measured 2,090 NPS-site↔OSM pairs, and federal↔OSM
   generally): unify at *display* time (group/prefer the richer row in search
   results and sheets, suppress twin pins) or at *import* time (a keeper
   hierarchy)? Display-time preserves all data and fits the id-bridge
   architecture; import-time is simpler but lossy.
