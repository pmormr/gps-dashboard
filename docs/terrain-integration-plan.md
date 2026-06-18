# Terrain Integration Plan (frontend)

## Context

The terrain DEM archive is built, shipped, and serving on the Pi
(`GET /tiles/terrain.pmtiles`, 104.7 GB, z0–12, Terrarium-encoded — see
`docs/terrain-tiles-plan.md`). Nothing in the live `/` view consumes it yet.
The only thing that has ever *rendered* it is the throwaway smoke-test page
`static/dev-terrain.html`.

This plan wires terrain into the actual dashboard: a tilted, 3D view of the
GPS history draped on real topography — the primary use case being reviewing
mountain drives where the track currently floats at sea level on a flat map.

The blocker is structural, not cosmetic: the map is **Leaflet** with the OSM
vector basemap embedded via the `maplibre-gl-leaflet` plugin. Leaflet cannot
pitch/tilt or render a terrain mesh. So this effort **drops Leaflet and moves
to pure MapLibre GL**, then layers terrain on top.

## Current architecture (what we're migrating from)

Confirmed by reading the code, not assumed:

- **`static/js/map.js`** is a self-contained `MapView` module — a façade. It
  owns a Leaflet `L.map`, renders the OSM vector basemap through
  `L.maplibreGL({ style })` (the plugin embeds a MapLibre map as a Leaflet
  layer), renders USGS as an `L.tileLayer`, and draws all overlays (trail,
  range highlights, pins, endpoints) as Leaflet `layerGroup` / `polyline` /
  `divIcon` markers in stacked panes.
- **`MapView`'s public API is the contract every other module depends on:**
  `init, showTrack, clearTrack, fitToTrack, zoomTo, invalidateSize,
  setRefreshMode, setLayer, getVectorBase, onVectorBase, clearAnnotations,
  addRangeOverlay, addPinOverlay`.
- **`timeline.js`, `annotations.js`, `app.js` contain zero Leaflet calls** —
  they only call `MapView.*`. This is the lever: keep the API stable and they
  don't change.
- **`labels.js`** is the one other Leaflet-aware consumer. It reaches the inner
  GL map via the plugin: `MapView.getVectorBase().getMaplibreMap()`, then drives
  the Protomaps style's `pois` and `roads_labels_minor` layers. Only the
  *accessor* is plugin-specific; the layer manipulation is plain MapLibre and
  carries over unchanged.
- **`templates/index.html`** loads, in order: `leaflet.js`, `maplibre-gl.js`
  (v5.24.0), `pmtiles.js`, `leaflet-maplibre-gl.js`, `nouislider`, then the app
  JS. Leaflet + the plugin are the only includes that go away.
- **Vendored MapLibre GL JS is v5.24.0** (from the bundle's license header).
  5.x is required for `line-elevation-reference` (track draping) and ships
  `TerrainControl` + terrain-aware markers. No re-vendor needed.

So the migration surface is small and well-bounded: **rewrite `map.js`,
adapt one accessor in `labels.js`, trim `index.html`/CSS, delete the Leaflet
vendor files.** The risk is depth (getting terrain + draping + style-swap
right), not breadth.

## Guiding constraints (carried from the project)

- **Offline-first.** Everything renders from Pi-local assets: vendored MapLibre
  5.24.0, `pmtiles.js`, the Protomaps style/glyphs/sprite, the OSM + terrain
  PMTiles archives, and the USGS raster cache. No CDN at runtime. The offline
  devtools gate from prior efforts is a success criterion here too.
- **Mobile-first.** The phone is the primary client over van WiFi. Terrain
  rendering must stay smooth on the phone GPU; this is the headline risk and
  gets an explicit on-device test phase.
- **No regressions.** Everything the current 2D view does (trail, fit-to-track,
  live re-fetch without view-jerk, annotations overlays, pins, USGS basemap,
  refresh mode, labels panel) must keep working. Pitch/terrain is additive.
- **Vendor-only deps.** If anything new is needed it gets vendored into
  `static/vendor/`. Nothing new is anticipated (MapLibre 5.24.0 already has
  terrain, hillshade, controls).

## Key decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Map engine | **Pure MapLibre GL, drop Leaflet** | Leaflet can't tilt/pitch or mesh terrain; the plugin was always a stopgap. MapLibre is already loaded. |
| 2 | Preserve `MapView` API | **Yes — same public methods** | `timeline/annotations/app` stay untouched; the rewrite is internal to `map.js`. Keep `invalidateSize` as an alias for `resize()` to avoid editing `app.js`. |
| 3 | Basemap switching | **`map.setStyle()` per basemap + idempotent `reinstallOverlays()` on `styledata`** | MapLibre-idiomatic. Vector OSM = the Protomaps style; USGS = a minimal style with one `raster` source. Terrain/track/annotation sources + `setTerrain` are re-added by one handler after every style load, so they survive switches. |
| 4 | Terrain vs hillshade sources | **Two separate `raster-dem` sources, same PMTiles URL** | Per the terrain-tiles Session 1 note: sharing one source between `setTerrain` and a `hillshade` layer degrades rendering. Same bytes, two GPU textures. |
| 5 | Track draping | **GeoJSON `line` layer + `line-elevation-reference: 'ground'`** | Native 5.x feature; the line follows the mesh instead of cutting through ridges. |
| 6 | Markers (endpoints, pins, annotations) | **`maplibregl.Marker` with ported HTML** (dot / amber teardrop) | Closest to the existing `divIcon` look; markers are terrain-aware in 5.x. Symbol-layer fallback if occlusion/anchor behavior is wrong (see Risks). |
| 7 | Coordinate order | **`map.js` flips `[lat,lon]`→`[lng,lat]` internally** | Leaflet is lat-first, MapLibre is lng-first. Contain the flip in the façade so `geo.js` / point objects are untouched. |
| 8 | Default state + 3D toggle | **Default 2D (flat); opt-in 3D toggle, available for both vector OSM and USGS** | Matches today's flat default; 3D is a deliberate choice. The toggle drives `setTerrain` on/off and is basemap-independent — whichever basemap is active (vector cartography or USGS raster) drapes on the mesh. Toggle state persists across basemap switches. |
| 9 | Rollout | **One shot — no staged release** | Single user, no compatibility concerns. Build the full migration in one pass and validate the end state (Phase 8). The phases below are a build-order checklist, not separate releases. |

## Target architecture

One `maplibregl.Map` in `#map`. `MapView` keeps its public API; internals:

```
maplibregl.Map (#map)
├── style: vector (Protomaps) OR raster (USGS), swapped via setStyle()
├── reinstallOverlays()  ← runs on every 'styledata' (idempotent add-if-absent)
│   ├── raster-dem source 'terrain-dem'   → pmtiles://…/tiles/terrain.pmtiles
│   ├── raster-dem source 'hillshade-dem' → (same URL, separate source)
│   ├── map.setTerrain({ source:'terrain-dem', exaggeration })  (only when 3D on)
│   ├── hillshade layer (optional, from 'hillshade-dem', when 3D on)
│   ├── GeoJSON source 'track' + line layer (line-elevation-reference: ground)
│   └── GeoJSON source 'annotations-range' + line layer
├── HTML markers: endpoints, annotation pins, drop-pins (maplibregl.Marker)
└── controls: NavigationControl({visualizePitch:true}) + terrain/exaggeration panel
```

Basemap switch = build the target style object, `setStyle(style)`, and the
`styledata` handler re-installs everything above the basemap. The Protomaps
style object is still fetched once and absolutized (sprite/glyphs against
`location.origin`) exactly as `map.js` does today.

### How terrain renders: mesh vs texture

Worth stating explicitly to avoid a common misconception: the Terrarium RGB is
**elevation, not color**. As a `raster-dem` source it drives the *shape* of the
mesh — MapLibre never paints those RGB values onto the map. The visible surface
is always whatever basemap drapes over the mesh; shown raw, the DEM is garish
noise (which is why the smoke test added a hillshade layer just to read the bare
mesh). Both basemaps drape:

- **OSM + 3D:** the vector cartography drapes on the mesh; an optional hillshade
  layer adds the relief shading the flat style lacks.
- **USGS + 3D:** the topo raster drapes on the mesh. USGS topo already bakes in
  relief shading, so it reads as 3D the most naturally for mountains.

The DEM is geometry-only in both cases. The 3D toggle is therefore
basemap-independent: it adds/removes `setTerrain` (+ optional hillshade) under
whichever basemap is loaded.

## Phases

### Phase 0 — Prereqs (mostly already true)

- [ ] Confirm MapLibre 5.24.0 vendored (done — license header). No re-vendor.
- [ ] Confirm the terrain route serves on the dev target (done on Pi: 206
      ranges). For local dev, set `GPS_TERRAIN_PMTILES_PATH` to a small archive
      (the Colorado calibration `.pmtiles`) so dev doesn't need the 104 GB file.
- [ ] Keep `static/dev-terrain.html` as the reference for the working
      `raster-dem` + `terrarium` source config.

### Phase 1 — Stand up the pure-MapLibre base (replaces Leaflet)

The riskiest structural step and the foundation for everything after; build it
first. End state is 2D feature-parity with today's Leaflet view (default load
is flat), now running on `maplibregl.Map`.

- [ ] Rewrite `MapView.init` to create a `maplibregl.Map` instead of `L.map`,
      with the vector Protomaps style as the initial style (same fetch +
      absolutize logic).
- [ ] Implement `setStyle`-based basemap switching (`setLayer`) with the
      `reinstallOverlays()` handler; USGS becomes a one-`raster`-source style.
- [ ] Port `showTrack` / `clearTrack` / `fitToTrack` to a GeoJSON `track`
      source + `line` layer; `fitBounds`/`zoomTo`/`invalidateSize`→`resize`.
      Keep the live-tick fit-skip behavior.
- [ ] Port endpoints + `addPinOverlay` + `addRangeOverlay` (flat, no terrain
      yet) so annotations work.
- [ ] Wire `setRefreshMode` for USGS (`?refresh=1` on the raster source tiles;
      no-op for vector).
- [ ] Verify 2D parity with today's view (trail, fit, live re-fetch, USGS,
      annotations) before layering terrain on top.

### Phase 2 — Terrain mesh + hillshade

- [ ] In `reinstallOverlays`, add the two `raster-dem` sources; apply
      `map.setTerrain(...)` + the optional hillshade only when 3D is enabled.
- [ ] Default load stays 2D (no terrain, pitch 0). Terrain is gated entirely on
      the 3D toggle (Phase 6) and works under both basemaps.
- [ ] Confirm terrain + the 3D toggle state survive basemap switches (re-added
      on `styledata`).

### Phase 3 — Drape the GPS track

- [ ] Add `line-elevation-reference: 'ground'` to the track line layer so it
      follows the mesh. Verify on a known mountain drive.
- [ ] Apply the same to the annotation range line layer.

### Phase 4 — Markers on terrain

- [ ] Port the dot (endpoints) and amber teardrop (pins) `divIcon` HTML to
      `maplibregl.Marker` custom elements; port tooltips (`bindTooltip` →
      `Popup` on hover, or title attr).
- [ ] Verify markers sit on the mesh and occlude correctly behind ridges; if
      not, fall back to symbol layers (Risk #1).

### Phase 5 — Labels panel

- [ ] Change the `MapView.getVectorBase()` contract to return the MapLibre map
      itself (or null when USGS active) instead of a plugin layer.
- [ ] Update `labels.js`: drop `getMaplibreMap()`, call `setFilter` /
      `setLayerZoomRange` / `setPaintProperty` directly on the map. The
      `pois` / `roads_labels_minor` logic is unchanged. Keep the `styledata`
      re-apply so settings survive basemap + style swaps.

### Phase 6 — Pitch / terrain UI controls

- [ ] Add `NavigationControl({ visualizePitch: true })` (compass + pitch).
- [ ] Add a floating panel (mirror the `⚙ Labels` / `🚩 Marks` pattern):
      a **3D toggle (default off)** that enables/disables terrain + pitch over
      whichever basemap is active, plus a terrain exaggeration slider (shown
      when 3D is on). Confirm two-finger pitch works on the phone.
- [ ] Re-point the `⊕` FAB (zoom to current fix) at `map.flyTo`/`easeTo`.

### Phase 7 — Remove Leaflet

- [ ] Drop `leaflet.js`, `leaflet.css`, `leaflet-maplibre-gl.js` from
      `index.html`. Remove `.leaflet-*` rules from `app.css`; confirm the
      floating overlays still position over the MapLibre canvas.
- [ ] Delete `static/vendor/leaflet/` and the plugin file (after the offline +
      phone tests pass — keep them until then for a one-line rollback).
- [ ] Move the MapLibre attribution (OSM/Protomaps + USGS + Mapzen/USGS-NED for
      terrain) into MapLibre's `AttributionControl`.

### Phase 8 — Offline + phone validation

- [ ] Desktop: DevTools offline mode + dev server → basemap, terrain, track all
      render with no external requests.
- [ ] Phone over van WiFi against the Pi: pan/zoom/pitch a mountain drive;
      confirm frame rate is acceptable and terrain byte-range fetches don't
      stall the UI. This is the real go/no-go for mobile.
- [ ] Re-run a quick USGS + vector + flatten pass to confirm no regressions.

### Phase 9 — Documentation

- [ ] Update `CLAUDE.md` Frontend + Basemaps sections: the map is now pure
      MapLibre (Leaflet gone), terrain is live, the new pitch/terrain panel
      exists. Remove the `maplibre-gl-leaflet` references.
- [ ] Record results + any deviations in this plan's Results section.

## Out of scope

- **3D buildings** — the Protomaps "light" basemap carries no building heights;
  needs a schema swap or supplementary source. Separate effort.
- **3DEP higher-res terrain** for chosen regions (Rockies/Sierra/Cascades) via
  `rio-rgbify` — same MapLibre integration, different archive. Tracked in
  `docs/terrain-tiles-plan.md`.
- **Sky/atmosphere, fog, advanced lighting** — nice-to-have polish, not core.
- **Changing the data model, API, or any non-map UI** (time picker, slider,
  drawer, sensors). Untouched.

## Success criteria (go/no-go)

1. With the 3D toggle on, both the vector OSM and USGS basemaps render draped on
   a tiltable terrain mesh, with the GPS track on the surface (not floating).
2. Full feature parity with the current 2D view: trail, fit-to-track, live
   re-fetch without view-jerk, annotations (ranges + pins), USGS basemap,
   refresh mode, labels panel.
3. Default load is the current flat 2D experience; the 3D toggle adds/removes
   terrain + pitch over either basemap and persists across basemap switches.
4. Renders fully offline (DevTools offline gate) and performs acceptably on the
   phone over van WiFi.
5. Leaflet and the plugin are removed from the page and the vendor tree.

## Open questions / risks

1. **Marker terrain behavior (Risk #1).** ~~Whether `maplibregl.Marker` HTML
   markers correctly sit on and occlude behind the mesh in 5.24.0.~~ Largely
   resolved during Phase 0: MapLibre v2+ clamps markers to the ground
   automatically. Still confirm occlusion behind ridges visually in Phase 8;
   symbol-layer fallback remains the escape hatch if needed.
2. **Phone GPU performance.** The headline risk. 104 GB of terrain tiles
   byte-ranged over van WiFi, meshed on a phone GPU. Mitigations if it's rough:
   cap pitch, lower default exaggeration, limit terrain `maxzoom`, or
   pre-warm/limit tile fetches. Decided empirically in Phase 8.
3. **`setStyle` overlay loss.** Every `setStyle` drops added sources/layers;
   the `reinstallOverlays()` handler must be correct and idempotent or basemap
   switches will lose the track/terrain. Well-trodden MapLibre pattern, but the
   main place bugs will hide.
4. **Live re-fetch + terrain.** The 30s live tick updates the `track` source; it
   must not reset pitch/bearing or jerk the camera (today it skips `fitBounds`).
   Preserve that with `setData` on the GeoJSON source rather than re-adding.
5. **USGS raster maxzoom (16) vs terrain (12).** Cosmetic mismatch at deep zoom
   when draping topo on terrain; acceptable.

## References

- Sibling/prereq: `docs/terrain-tiles-plan.md` (archive build + the "Notes for
  the integration follow-on" section — separate hillshade source, no `?refresh`
  for terrain, MapLibre version note).
- Working source config: `static/dev-terrain.html` (the proven `raster-dem` +
  `terrarium` smoke test, retargeted to the continental NA archive).
- MapLibre terrain: https://maplibre.org/maplibre-style-spec/terrain/
- `line-elevation-reference`:
  https://maplibre.org/maplibre-style-spec/layers/#line-elevation-reference
- Current façade to preserve: `static/js/map.js` (`MapView` public API).

## Results

Implemented in one session (Phases 1–7, 9). Phase 8 (offline + on-device
validation) is pending — it needs a real WebGL context on the Pi/phone, which
can't be exercised from the dev box here.

### What landed

- **Phase 1 + 5 — pure MapLibre (`d345692`).** `map.js` rewritten to a single
  `maplibregl.Map`; `MapView`'s public API is unchanged, so `timeline.js`,
  `annotations.js`, `app.js` were untouched. Basemaps swap via `setStyle()`;
  an idempotent, re-entrancy-guarded `styledata` handler (`reinstallOverlays`)
  re-adds the track, annotation-range, and DEM sources after each swap. Track
  + ranges are GeoJSON `line` layers; endpoints/pins are `maplibregl.Marker`
  DOM elements (survive style swaps, clamp to terrain). `[lat,lon]→[lng,lat]`
  flip contained in the façade. `labels.js` now drives the map returned by
  `getVectorBase()` directly (no more plugin `getMaplibreMap()`), with a `pois`
  guard so it no-ops under a raster basemap.
- **Phase 2 + 3 — terrain.** Two `raster-dem` sources (mesh + hillshade, same
  archive). `setTerrain` + the vector-only hillshade are gated on the 3D flag.
  Track draping needs no code (see deviation below).
- **Phase 4 — markers.** Ported dot/teardrop `divIcon` HTML to `Marker`
  elements; tooltips are `title` attrs (markers) + a hover `Popup` (ranges).
- **Phase 6 — 3D UI (`11776d0`).** A 🏔 3D floating panel (Labels/Marks
  pattern): a 3D toggle + exaggeration slider. 2D stays flat/north-up;
  rotate + pitch gestures are disabled until 3D is on, and toggling off
  re-flattens.
- **Phase 7 — Leaflet off the page (`3a7bb9b`).** Removed the leaflet.js /
  leaflet.css / leaflet-maplibre-gl.js includes and swapped `.leaflet-*` CSS
  for `.maplibregl-*`. **Vendor files (`static/vendor/leaflet/` + the plugin)
  are still on disk** — delete them once Phase 8 passes.
- **Phase 9 — docs.** CLAUDE.md Frontend/Basemaps/structure updated; this
  Results section.

### Deviations from the plan

1. **`line-elevation-reference` (decision #5 / Phase 3) does not exist in
   MapLibre.** It's a Mapbox GL JS v3.8+ property for *elevating* lines above
   ground (`line-z-offset`), confirmed absent from the vendored 5.24.0 build.
   It's also unnecessary: MapLibre drapes 2D `line`/`fill` layers onto the mesh
   automatically (render-to-texture), so the track follows terrain for free.
   This **resolves Risk #1** too — `Marker`s clamp to ground natively in v2+.
2. **No standalone `NavigationControl` (Phase 6).** The `.right-panel-stack`
   sits at `z-index: 700` and would bury a corner control; on a phone, zoom is
   pinch and pitch is a two-finger gesture. The 3D panel toggle + auto-reflatten
   covers the intent. Easy to add back if the compass is wanted.

### Server-side validation done here

Against the dev server (USGS online + `colorado.pmtiles` as the dev DEM):
index + all JS + style.json + maplibre serve 200; `/tiles/terrain.pmtiles`
honors `Range` (206); the served HTML has zero Leaflet references and includes
the 3D panel. No local `northamerica.pmtiles`, so the vector basemap and all
client-side WebGL rendering (draping, mesh, track-on-terrain, marker occlusion,
phone GPU perf) are unverified — that is exactly Phase 8.

### Phase 8 checklist (on the Pi / phone)

- [ ] Desktop DevTools offline: vector basemap + terrain + track render with no
      external requests.
- [ ] Phone over van WiFi vs. the Pi: pan/zoom/pitch a mountain drive; frame
      rate acceptable, terrain byte-range fetches don't stall the UI. **Go/no-go.**
- [ ] USGS + vector + flatten regression pass.
- [ ] If green: delete `static/vendor/leaflet/` + `leaflet-maplibre-gl.js`, drop
      the "pending deletion" notes in CLAUDE.md, and move attribution into the
      control (currently `AttributionControl` already carries OSM/Protomaps +
      Mapzen/USGS-NED).
