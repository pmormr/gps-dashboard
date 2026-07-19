/**
 * Map façade. One maplibregl.Map drives the whole
 * view; this module is the only place that touches MapLibre directly. The public
 * API (`init`, `showTrack`, …) is the contract the Svelte chrome + the Selection
 * store depend on — keep it stable.
 *
 * Basemaps swap via map.setStyle(); overlays (track, annotation ranges, terrain
 * DEM sources, setTerrain) are re-installed on every style load by an idempotent
 * handler, since setStyle drops everything not in the new style. Markers are DOM
 * (maplibregl.Marker) and survive style swaps, so they're managed separately.
 *
 * In the SPA the instance is kept alive across routes (see mapHost.ts) — the map
 * is mounted once and re-parented, never re-created.
 */

import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Feature, FeatureCollection, LineString, Point } from 'geojson'
import type { GeoJSONSource, Map as MlMap, Marker, Popup } from 'maplibre-gl'
import { Protocol } from 'pmtiles'

import { emptyFC, escapeHtml, fmtAltitude, fmtDate, fmtDurationSecs, fmtTime } from './geo'
import { DEFAULT_BASEMAP_THEME, type BasemapTheme } from './labels'
import { prefetchTiles } from './prefetch'

/**
 * The three.js elevated-data overlay (drone tracks float at MSL altitude). It's a
 * separate imperative island (overlay3d.ts) wired in via `setOverlay3D` so map.ts
 * doesn't depend on three; null until the drone controller lazy-imports and wires it.
 */
export interface Overlay3DLine {
  coords: [number, number, number][]
  color?: string
  opacity?: number
  width?: number
  meta?: unknown
}
export interface Overlay3DApi {
  installLayer(map: MlMap): void
  setLines(groupId: string, lines: Overlay3DLine[], opts?: { color?: string; opacity?: number; width?: number }): void
  clear(groupId: string): void
  pick(px: number, py: number): { meta: unknown; lngLat: [number, number] } | null
  setExaggeration(k: number): void
}
let overlay3d: Overlay3DApi | null = null
/** Register the three.js overlay (called by overlay3d.ts once it's imported). */
export function setOverlay3D(impl: Overlay3DApi): void {
  overlay3d = impl
}

/** A drone flight as returned by /api/drone/flights. */
export interface DroneFlight {
  model: string
  model_code: string
  media_path?: string
  source_name?: string
  first_fix_utc: string
  last_fix_utc: string
  n_points: number
  points?: { lon: number; lat: number; abs_alt: number | null }[]
}

/** The popup payload carried on each drone line via Overlay3D `meta`. */
interface DroneMeta {
  model: string
  model_code: string
  media_path: string
  source_name: string
  first_fix_utc: string
  last_fix_utc: string
  n_points: number
  alt_min: number | null
  alt_max: number | null
}

/** A point shown on the trail (the relevant subset of geo.TrackPoint). */
interface MapPoint {
  lat: number
  lon: number
  timestamp: string
  kind?: string
  n_raw?: number
  dwell_start?: string | null
  dwell_end?: string | null
}

// Raster tile layers proxied/cached by Flask (mirrors the raster entries in
// api/tile_layers.py). The OSM basemap is vector (served at /tiles/osm.pmtiles),
// so USGS is the only raster layer left here.
const TILE_LAYERS: Record<string, { label: string; attribution: string; maxzoom: number }> = {
  usgs: {
    label: 'USGS Topo',
    attribution: '<a href="https://www.usgs.gov/">USGS</a> The National Map',
    maxzoom: 16,
  },
}

const VECTOR_ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, ' +
  '<a href="https://protomaps.com">Protomaps</a>'
const TERRAIN_ATTRIBUTION =
  '<a href="https://github.com/tilezen/joerd">Mapzen</a> / USGS NED (terrain)'

// Single immutable terrain DEM archive, byte-ranged by pmtiles.js. Same URL feeds
// two raster-dem sources (mesh + hillshade); sharing one source between setTerrain
// and a hillshade layer degrades rendering, so they stay separate.
const TERRAIN_PMTILES_URL = 'pmtiles://' + location.origin + '/tiles/terrain.pmtiles'

function rasterTileUrl(layer: string, refresh: boolean): string {
  return `/tiles/${layer}/{z}/{x}/{y}.png` + (refresh ? '?refresh=1' : '')
}

// Register the pmtiles:// protocol once for the MapLibre instance.
const pmtilesProtocol = new Protocol()
maplibregl.addProtocol('pmtiles', pmtilesProtocol.tile)

// Fetch + patch a vector theme's style on first use (then cached). MapLibre
// rejects a root-relative sprite URL, so sprite/glyphs are absolutized against
// location.origin (portable across localhost and the LAN IP); the pmtiles
// source URL stays root-relative.
const vectorStyleCache = new Map<BasemapTheme, maplibregl.StyleSpecification>()
// The unified POI icon sprite (SDF — tintable), second sprite beside the
// style's own: GL refs are 'poi:<name>' (see icons.ts).
const POI_SPRITE = '/static/vendor/basemap/sprite-poi/poi'

async function loadVectorStyle(theme: BasemapTheme): Promise<maplibregl.StyleSpecification> {
  const cached = vectorStyleCache.get(theme)
  if (cached) return cached
  const s = await (await fetch(`/static/vendor/basemap/style-${theme}.json`)).json()
  s.sprite = [
    { id: 'default', url: location.origin + s.sprite },
    { id: 'poi', url: location.origin + POI_SPRITE },
  ]
  s.glyphs = location.origin + s.glyphs
  vectorStyleCache.set(theme, s)
  return s
}

// Warm the boot theme so the first map mount doesn't wait on the fetch.
void loadVectorStyle(DEFAULT_BASEMAP_THEME)

// Minimal style the map boots with so `map` exists synchronously; replaced by the
// real basemap as soon as its style is ready.
const BOOTSTRAP_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#0f172a' } }],
}

// setStyle receives a deep copy so MapLibre never mutates the cached document.
function cloneStyle(doc: maplibregl.StyleSpecification): maplibregl.StyleSpecification {
  return JSON.parse(JSON.stringify(doc))
}

function buildRasterStyle(layer: string, refresh: boolean): maplibregl.StyleSpecification {
  const cfg = TILE_LAYERS[layer]
  return {
    version: 8,
    // The pin overlays reference 'poi:' icons, so the raster base carries the
    // POI sprite too (no 'default' sprite — raster has no styled symbols).
    sprite: [{ id: 'poi', url: location.origin + POI_SPRITE }],
    sources: {
      [layer]: {
        type: 'raster',
        tiles: [rasterTileUrl(layer, refresh)],
        tileSize: 256,
        maxzoom: cfg.maxzoom,
        attribution: cfg.attribution,
      },
    },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': '#0f172a' } },
      { id: layer, type: 'raster', source: layer },
    ],
  }
}

const TRACK_COLOR = '#ef4444'
const RANGE_COLOR = '#22d3ee'

// Per-model drone-track colors, keyed by the FC#### model_code. Keep in sync with
// the legend swatches in the Layers panel.
const DRONE_COLORS: Record<string, string> = {
  FC9313: '#a855f7', // DJI Mini 5 Pro — purple
  FC8485: '#f97316', // DJI Avata 2 — orange
  FC8671: '#ec4899', // DJI Neo — pink
}
const DRONE_DEFAULT_COLOR = '#94a3b8'

function lineFC(points: MapPoint[]): FeatureCollection {
  if (points.length < 2) return emptyFC()
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: points.map((p) => [p.lon, p.lat]) },
        properties: {},
      },
    ],
  }
}

function droneColor(modelCode: string): string {
  return DRONE_COLORS[modelCode] || DRONE_DEFAULT_COLOR
}

// A stop → a Point feature carrying dwell minutes. lineFC already routes the trail
// polyline through the stop centroid; this marks the pause node on top of it.
function stopPointToFeature(p: MapPoint): Feature<Point> {
  const dwellMin =
    p.dwell_start && p.dwell_end
      ? (new Date(p.dwell_end).getTime() - new Date(p.dwell_start).getTime()) / 60000
      : 0
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
    properties: { dwell_min: dwellMin, n_raw: p.n_raw || 0 },
  }
}

// One floating 3D polyline per flight for Overlay3D: [lon, lat, abs_alt] vertices
// (abs_alt is MSL, the same datum as the terrain DEM), colored by model, with the
// popup payload as `meta`. Points missing abs_alt are dropped so a gap doesn't snap
// a vertex to z=0.
function droneToLine(flight: DroneFlight): Overlay3DLine {
  const pts = (flight.points || []).filter((p) => p.abs_alt != null)
  let altMin: number | null = null
  let altMax: number | null = null
  for (const p of pts) {
    const a = p.abs_alt as number
    if (altMin == null || a < altMin) altMin = a
    if (altMax == null || a > altMax) altMax = a
  }
  const meta: DroneMeta = {
    model: flight.model,
    model_code: flight.model_code,
    media_path: flight.media_path || '',
    source_name: flight.source_name || '',
    first_fix_utc: flight.first_fix_utc,
    last_fix_utc: flight.last_fix_utc,
    n_points: flight.n_points,
    alt_min: altMin,
    alt_max: altMax,
  }
  return {
    coords: pts.map((p) => [p.lon, p.lat, p.abs_alt as number]),
    color: droneColor(flight.model_code),
    meta,
  }
}


// ── DOM marker elements (terrain-aware; clamp to ground automatically) ──

function dotElement(): HTMLDivElement {
  const el = document.createElement('div')
  el.style.cssText =
    'width:10px;height:10px;border-radius:50%;background:#3b82f6;' +
    'border:2px solid #fff;box-shadow:0 0 4px rgba(0,0,0,.5)'
  return el
}

function pinElement(): HTMLDivElement {
  const el = document.createElement('div')
  el.style.cssText =
    'width:14px;height:14px;border-radius:50% 50% 50% 0;background:#f59e0b;' +
    'border:2px solid #fff;transform:rotate(-45deg);box-shadow:0 0 4px rgba(0,0,0,.5)'
  return el
}

function puckElement(): HTMLDivElement {
  const el = document.createElement('div')
  el.className = 'map-puck'
  return el
}

/** A camera pose the Drive follow loop hard-sets each frame. */
/** A tapped basemap pois mark, decoded from the tile feature. */
export interface BasemapPoiHit {
  /** planetiler feature id (icons.ts decodes it to a tier source_id), or null. */
  featureId: number | null
  name: string | null
  kind: string | null
  lon: number
  lat: number
}

export interface CameraPose {
  lat: number
  lon: number
  bearing: number
  pitch: number
  zoom: number
}

export const MapView = (() => {
  let map: MlMap | undefined
  let currentLayer = 'osm'
  let currentTheme: BasemapTheme = DEFAULT_BASEMAP_THEME
  let currentRefresh = false
  let onVectorBaseCb: ((map: MlMap) => void) | null = null

  // 3D / terrain state (driven by the 3D toggle UI; default flat 2D).
  let terrainEnabled = false
  let exaggeration = 1.3

  // Overlay state, the source of truth re-applied after every style load.
  let trackData: FeatureCollection = emptyFC()
  // Drive-view breadcrumb: the raw-seeded trailing trail, live-extended.
  let breadcrumbData: FeatureCollection = emptyFC()
  let rangeFeatures: Feature<LineString>[] = []
  let stopFeatures: Feature<Point>[] = []
  let lastTrackPoints: MapPoint[] = []
  // Phone-history overlay: prebuilt GeoJSON (color-by-mode is baked into each
  // feature's `color` by phone.ts, so these layers are domain-free).
  let phonePathData: FeatureCollection = emptyFC()
  let phoneVisitData: FeatureCollection = emptyFC()
  // Places overlay: prebuilt pin GeoJSON (per-kind color baked in by
  // places.ts); clicks hand the feature's row id to the registered callback
  // (the detail sheet), keeping this layer domain-free too.
  let placeData: FeatureCollection = emptyFC()
  let placeClickCb: ((id: number) => void) | null = null
  // Basemap pois marks are tap targets too (the id bridge): a click hands the
  // decoded tile attrs to the registered callback, which resolves it to a
  // tier row (or the minimal sheet). Kept domain-free like the pin callback.
  let basemapPoiClickCb: ((hit: BasemapPoiHit) => void) | null = null
  // Search-results overlay: the Places view's pushed result set. Uniform
  // accent styling (not per-kind) so results read as "what you searched for"
  // over the browse pins; shares the place click/hover handlers (same rows,
  // same detail sheet).
  let searchData: FeatureCollection = emptyFC()
  const endpointMarkers: Marker[] = []
  const pinMarkers: Marker[] = []

  let installing = false // re-entrancy guard for reinstallOverlays
  let rangePopup: Popup | null = null
  let dronePopup: Popup | null = null
  let phonePopup: Popup | null = null
  let placePopup: Popup | null = null

  // Hover-scrub ghost dot (a DOM marker — survives style swaps) + moveend
  // subscribers (the viewport-filter toggle). Callbacks are stashed in a set so
  // consumers can subscribe before/after init; init wires the single listener.
  let ghostMarker: Marker | null = null
  const moveEndCbs = new Set<() => void>()

  // Drive-view puck (another style-swap-surviving DOM marker) + user-gesture
  // subscribers. Gesture vs program: user input carries `originalEvent` on
  // movestart, the follow loop's per-frame jumpTo doesn't — that's what lets
  // "any pan/zoom suspends follow" coexist with a camera set 60×/s.
  let puckMarker: Marker | null = null
  const userMoveCbs = new Set<() => void>()

  // Drive-view destination pin (DOM marker) + long-press subscribers (dropped-pin
  // destinations). Long-press is hand-rolled: touchstart arms a timer that a move
  // past a slop radius, a second touch, or touchend cancels; desktop gets the
  // same callback from contextmenu (right-click).
  let destMarker: Marker | null = null
  const longPressCbs = new Set<(lat: number, lon: number) => void>()
  const LONG_PRESS_MS = 600
  const LONG_PRESS_SLOP_PX = 12

  function rangeFC(): FeatureCollection {
    return { type: 'FeatureCollection', features: rangeFeatures }
  }

  function stopsFC(): FeatureCollection {
    return { type: 'FeatureCollection', features: stopFeatures }
  }

  function addMarker(
    list: Marker[],
    element: HTMLElement,
    lat: number,
    lon: number,
    anchor: 'center' | 'bottom',
    title?: string,
  ): Marker {
    if (title) element.title = title
    const m = new maplibregl.Marker({ element, anchor }).setLngLat([lon, lat]).addTo(map!)
    list.push(m)
    return m
  }

  function clearMarkers(list: Marker[]): void {
    for (const m of list) m.remove()
    list.length = 0
  }

  // ── Style / overlay lifecycle ──

  function demSource(): maplibregl.RasterDEMSourceSpecification {
    return {
      type: 'raster-dem',
      url: TERRAIN_PMTILES_URL,
      tileSize: 256,
      encoding: 'terrarium',
      maxzoom: 12,
    }
  }

  function firstSymbolLayerId(m: MlMap): string | undefined {
    const layers = m.getStyle().layers || []
    const sym = layers.find((l) => l.type === 'symbol')
    return sym ? sym.id : undefined
  }

  // Add/remove the terrain mesh + (vector-only) hillshade to match the current 3D
  // state. Idempotent: only mutates when the desired state differs.
  function applyTerrain(): void {
    if (!map) return
    const m = map
    const wantHillshade = terrainEnabled && currentLayer === 'osm'
    const hasHillshade = !!m.getLayer('hillshade')
    if (wantHillshade && !hasHillshade) {
      m.addLayer(
        {
          id: 'hillshade',
          type: 'hillshade',
          source: 'hillshade-dem',
          paint: {
            'hillshade-exaggeration': 0.5,
            'hillshade-shadow-color': '#000',
            'hillshade-highlight-color': '#fff',
          },
        },
        firstSymbolLayerId(m),
      )
    } else if (!wantHillshade && hasHillshade) {
      m.removeLayer('hillshade')
    }

    const want = terrainEnabled ? { source: 'terrain-dem', exaggeration } : null
    const cur = m.getTerrain()
    const same =
      (!want && !cur) ||
      (!!want && !!cur && cur.source === want.source && cur.exaggeration === want.exaggeration)
    if (!same) m.setTerrain(want)
  }

  // Re-add every overlay setStyle dropped. Idempotent (add-if-absent) and
  // re-entrancy-guarded, so it can run on every styledata event safely. Driven off
  // styledata (not gated on isStyleLoaded): adding sources/layers only needs the
  // style document, present once setStyle resolves; isStyleLoaded() additionally
  // waits for source tile data, which for the byte-ranged vector pmtiles source can
  // lag past style.load and dropped the overlays entirely. The try/catch tolerates a
  // too-early call; the next styledata retries idempotently.
  /** Push GeoJSON into a map source; no-op if the map or source is absent. */
  function setGeoJSON(name: string, data: FeatureCollection): void {
    ;(map?.getSource(name) as GeoJSONSource | undefined)?.setData(data)
  }

  function reinstallOverlays(): void {
    if (installing || !map) return
    const m = map
    installing = true
    try {
      if (!m.getSource('terrain-dem')) m.addSource('terrain-dem', demSource())
      if (!m.getSource('hillshade-dem')) m.addSource('hillshade-dem', demSource())

      if (!m.getSource('ann-range')) m.addSource('ann-range', { type: 'geojson', data: rangeFC() })
      if (!m.getLayer('ann-range-line')) {
        m.addLayer({
          id: 'ann-range-line',
          type: 'line',
          source: 'ann-range',
          layout: { 'line-join': 'round', 'line-cap': 'round' },
          paint: { 'line-color': RANGE_COLOR, 'line-width': 6, 'line-opacity': 0.7 },
        })
      }
      // Phone history sits under the van track (added next), so the live trail
      // stays legible on top. Line color is data-driven (per-mode) off the feature.
      if (!m.getSource('phone-track')) {
        m.addSource('phone-track', { type: 'geojson', data: phonePathData })
      }
      if (!m.getLayer('phone-track-line')) {
        m.addLayer({
          id: 'phone-track-line',
          type: 'line',
          source: 'phone-track',
          layout: { 'line-join': 'round', 'line-cap': 'round' },
          paint: { 'line-color': ['get', 'color'], 'line-width': 2.5, 'line-opacity': 0.8 },
        })
      }
      if (!m.getSource('phone-visits')) {
        m.addSource('phone-visits', { type: 'geojson', data: phoneVisitData })
      }
      if (!m.getLayer('phone-visit-circle')) {
        m.addLayer({
          id: 'phone-visit-circle',
          type: 'circle',
          source: 'phone-visits',
          paint: {
            'circle-radius': 4,
            'circle-color': ['get', 'color'],
            'circle-stroke-color': '#334155',
            'circle-stroke-width': 1,
          },
        })
      }

      if (!m.getSource('track')) m.addSource('track', { type: 'geojson', data: trackData })
      if (!m.getLayer('track-line')) {
        m.addLayer({
          id: 'track-line',
          type: 'line',
          source: 'track',
          layout: { 'line-join': 'round', 'line-cap': 'round' },
          paint: { 'line-color': TRACK_COLOR, 'line-width': 3, 'line-opacity': 0.85 },
        })
      }

      // Stops as a constant-size dot on the trail — a "you stopped here" node, not
      // scaled by dwell (dwell reads off the timeline block). White stroke separates
      // the dot from the same-colored track line.
      if (!m.getSource('stops')) m.addSource('stops', { type: 'geojson', data: stopsFC() })
      if (!m.getLayer('stop-circle')) {
        m.addLayer({
          id: 'stop-circle',
          type: 'circle',
          source: 'stops',
          paint: {
            'circle-radius': 5,
            'circle-color': TRACK_COLOR,
            'circle-stroke-color': '#fff',
            'circle-stroke-width': 1.5,
          },
        })
      }

      // Drive breadcrumb above the history track/stops (the live trail is the
      // one that must stay legible while driving), below the place pins.
      // Puck-blue, vs the red history track.
      if (!m.getSource('breadcrumb')) {
        m.addSource('breadcrumb', { type: 'geojson', data: breadcrumbData })
      }
      if (!m.getLayer('breadcrumb-line')) {
        m.addLayer({
          id: 'breadcrumb-line',
          type: 'line',
          source: 'breadcrumb',
          layout: { 'line-join': 'round', 'line-cap': 'round' },
          paint: { 'line-color': '#3b82f6', 'line-width': 4, 'line-opacity': 0.85 },
        })
      }

      // Place pins sit on top of the trail — they're tap targets, and the
      // track line stays visible between them. Color is data-driven per kind.
      if (!m.getSource('places')) {
        m.addSource('places', { type: 'geojson', data: placeData })
      }
      if (!m.getLayer('place-circle')) {
        m.addLayer({
          id: 'place-circle',
          type: 'circle',
          source: 'places',
          paint: {
            'circle-radius': 9,
            'circle-color': ['get', 'color'],
            'circle-stroke-color': '#fff',
            'circle-stroke-width': 1.5,
          },
        })
      }
      // The unified icon language (icons.ts) on top of the category-colored
      // circle: SDF sprite refs baked into the feature ('poi:campsite'),
      // tinted white. Overlap allowed — the circle already marks the spot,
      // a collision-hidden glyph would read as a different (empty) pin kind.
      if (!m.getLayer('place-icon')) {
        m.addLayer({
          id: 'place-icon',
          type: 'symbol',
          source: 'places',
          layout: {
            'icon-image': ['get', 'icon'],
            'icon-size': 0.75,
            'icon-allow-overlap': true,
            'icon-ignore-placement': true,
          },
          paint: { 'icon-color': '#ffffff' },
        })
      }

      // Search results render above the browse pins: a soft halo under a
      // bigger accent dot, so the pushed result set stands out from the
      // category-colored overlay.
      if (!m.getSource('search-results')) {
        m.addSource('search-results', { type: 'geojson', data: searchData })
      }
      if (!m.getLayer('search-halo')) {
        m.addLayer({
          id: 'search-halo',
          type: 'circle',
          source: 'search-results',
          paint: {
            'circle-radius': 14,
            'circle-color': '#38bdf8',
            'circle-opacity': 0.25,
          },
        })
      }
      if (!m.getLayer('search-circle')) {
        m.addLayer({
          id: 'search-circle',
          type: 'circle',
          source: 'search-results',
          paint: {
            'circle-radius': 9,
            'circle-color': '#38bdf8',
            'circle-stroke-color': '#0c4a6e',
            'circle-stroke-width': 2,
          },
        })
      }
      if (!m.getLayer('search-icon')) {
        m.addLayer({
          id: 'search-icon',
          type: 'symbol',
          source: 'search-results',
          layout: {
            'icon-image': ['get', 'icon'],
            'icon-size': 0.75,
            'icon-allow-overlap': true,
            'icon-ignore-placement': true,
          },
          paint: { 'icon-color': '#0c4a6e' },
        })
      }

      // Drone tracks float at abs_alt as a three.js elevated-track layer (Overlay3D),
      // re-added here since setStyle drops it; onAdd rebuilds its scene from retained
      // data. Null until overlay3d.ts is wired in.
      overlay3d?.installLayer(m)

      // Sources are fresh after a style swap — push the current data back in.
      setGeoJSON('track', trackData)
      setGeoJSON('stops', stopsFC())
      setGeoJSON('breadcrumb', breadcrumbData)
      setGeoJSON('ann-range', rangeFC())
      setGeoJSON('phone-track', phonePathData)
      setGeoJSON('phone-visits', phoneVisitData)
      setGeoJSON('places', placeData)
      setGeoJSON('search-results', searchData)

      // Pitched-view tile cover: allow more distinct zoom levels on screen and a
      // larger high-pitch tile budget so the far field loads without a camera
      // nudge (no effect at pitch 0; values from the MapLibre LOD example,
      // verified to pull extra far-field detail at pitch ~78). Runs here so
      // every source — basemap, DEMs, overlays — gets the params on every
      // style (re)load.
      m.setSourceTileLodParams(4, 3)

      applyTerrain()
    } catch (e) {
      console.error('reinstallOverlays:', e)
    } finally {
      installing = false
    }
  }

  function handleStyleLoad(): void {
    reinstallOverlays()
    // setStyle replaced every layer — captured text-size originals are stale.
    labelSizeOriginals = null
    applyLabelScale()
    if (currentLayer === 'osm' && onVectorBaseCb && map) onVectorBaseCb(map)
  }

  function applyBasemap(layer: string): void {
    if (layer === 'osm') {
      const theme = currentTheme
      loadVectorStyle(theme).then((doc) => {
        // Stale-guard: the user may have switched base or theme mid-fetch.
        if (currentLayer === 'osm' && currentTheme === theme && map) map.setStyle(cloneStyle(doc))
      })
    } else if (map) {
      map.setStyle(buildRasterStyle(layer, currentRefresh))
    }
  }

  /** Swap the vector basemap theme (no-op while raster is active beyond recording it). */
  function setVectorTheme(theme: BasemapTheme): void {
    if (theme === currentTheme) return
    currentTheme = theme
    if (currentLayer === 'osm' && map) applyBasemap('osm')
  }

  // Range-name tooltip on hover. Registered once; the layer-scoped handler is a
  // no-op until the layer exists.
  function wireRangeTooltip(m: MlMap): void {
    rangePopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false })
    m.on('mouseenter', 'ann-range-line', () => {
      m.getCanvas().style.cursor = 'pointer'
    })
    m.on('mousemove', 'ann-range-line', (e) => {
      const name = e.features && e.features[0] && (e.features[0].properties.name as string)
      if (name) rangePopup!.setLngLat(e.lngLat).setText(name).addTo(m)
    })
    m.on('mouseleave', 'ann-range-line', () => {
      m.getCanvas().style.cursor = ''
      rangePopup!.remove()
    })
  }

  // Click a drone track → a popup with model, time span, altitude range, media path.
  // The 3D tracks live in Overlay3D (not a MapLibre layer), so picking goes through
  // Overlay3D.pick on map-level click/mousemove.
  function dronePopupHtml(p: DroneMeta): string {
    const durMs = new Date(p.last_fix_utc).getTime() - new Date(p.first_fix_utc).getTime()
    const alt =
      p.alt_min != null && p.alt_max != null
        ? `${fmtAltitude(p.alt_min)}–${fmtAltitude(p.alt_max)} MSL`
        : '—'
    const path = p.media_path
      ? `<div class="drone-popup-path" title="${escapeHtml(p.media_path)}">${escapeHtml(p.media_path)}</div>`
      : `<div class="drone-popup-path muted">${escapeHtml(p.source_name || 'no media path')}</div>`
    return (
      `<div class="drone-popup">` +
      `<div class="drone-popup-title">` +
      `<span class="drone-popup-swatch" style="background:${droneColor(p.model_code)}"></span>` +
      `${escapeHtml(p.model)}</div>` +
      `<div class="drone-popup-meta">${fmtDate(p.first_fix_utc)} · ${fmtTime(p.first_fix_utc)} → ${fmtTime(p.last_fix_utc)} · ${fmtDurationSecs(durMs / 1000)}</div>` +
      `<div class="drone-popup-meta">Alt ${alt} · ${p.n_points} pts</div>` +
      path +
      `</div>`
    )
  }

  function wireDronePopup(m: MlMap): void {
    dronePopup = new maplibregl.Popup({ closeButton: true, closeOnClick: true, maxWidth: '300px' })
    // Light hover affordance: only touch the cursor on hit-state transitions.
    let droneHover = false
    m.on('mousemove', (e) => {
      if (!overlay3d) return
      const hit = !!overlay3d.pick(e.point.x, e.point.y)
      if (hit && !droneHover) {
        m.getCanvas().style.cursor = 'pointer'
        droneHover = true
      } else if (!hit && droneHover) {
        m.getCanvas().style.cursor = ''
        droneHover = false
      }
    })
    m.on('click', (e) => {
      if (!overlay3d) return
      const hit = overlay3d.pick(e.point.x, e.point.y)
      if (hit && hit.meta) {
        dronePopup!.setLngLat(hit.lngLat).setHTML(dronePopupHtml(hit.meta as DroneMeta)).addTo(m)
      }
    })
  }

  // Click a phone-history visit pin → its prebuilt popup (phone.ts bakes the HTML
  // into the feature). Layer-scoped and registered once; a no-op until the layer
  // exists, like the range tooltip.
  function wirePhonePopup(m: MlMap): void {
    phonePopup = new maplibregl.Popup({ closeButton: true, closeOnClick: true, maxWidth: '260px' })
    m.on('mouseenter', 'phone-visit-circle', () => {
      m.getCanvas().style.cursor = 'pointer'
    })
    m.on('mouseleave', 'phone-visit-circle', () => {
      m.getCanvas().style.cursor = ''
    })
    m.on('click', 'phone-visit-circle', (e) => {
      const feature = e.features && e.features[0]
      const html = feature && (feature.properties?.popup as string | undefined)
      if (html) phonePopup!.setLngLat(e.lngLat).setHTML(html).addTo(m)
    })
  }

  // Basemap pois marks: cursor affordance + the decoded tile attrs to the
  // registered callback on click. The feature id is planetiler-encoded
  // (icons.ts decodes it to a tier source_id); name/kind ride along for the
  // unresolved-mark minimal sheet. Vector-base only (the layer doesn't exist
  // on raster; layer-scoped listeners no-op there).
  function wirePoisInteraction(m: MlMap): void {
    m.on('mouseenter', 'pois', () => {
      m.getCanvas().style.cursor = 'pointer'
    })
    m.on('mouseleave', 'pois', () => {
      m.getCanvas().style.cursor = ''
    })
    m.on('click', 'pois', (e) => {
      const f = e.features && e.features[0]
      if (!f || !basemapPoiClickCb) return
      // A pin drawn over the mark wins the tap — both layer handlers fire for
      // the same click, and the pin's sheet is the richer one.
      const pinLayers = ['place-circle', 'search-circle'].filter((l) => m.getLayer(l))
      if (pinLayers.length && m.queryRenderedFeatures(e.point, { layers: pinLayers }).length) {
        return
      }
      basemapPoiClickCb({
        featureId: typeof f.id === 'number' ? f.id : null,
        name: (f.properties?.name as string | undefined) ?? null,
        kind: (f.properties?.kind as string | undefined) ?? null,
        lon: e.lngLat.lng,
        lat: e.lngLat.lat,
      })
    })
  }

  // Place pins: name tooltip on hover, row id to the registered callback on
  // click (the detail sheet lives in Svelte). Layer-scoped and registered once; a
  // no-op until the layer exists, like the range tooltip. Search-result pins
  // carry the same row shape, so they share the handlers.
  function wirePlaceInteraction(m: MlMap): void {
    placePopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false })
    for (const layer of ['place-circle', 'search-circle']) {
      m.on('mouseenter', layer, () => {
        m.getCanvas().style.cursor = 'pointer'
      })
      m.on('mousemove', layer, (e) => {
        const name = e.features && e.features[0] && (e.features[0].properties.name as string)
        if (name) placePopup!.setLngLat(e.lngLat).setText(name).addTo(m)
      })
      m.on('mouseleave', layer, () => {
        m.getCanvas().style.cursor = ''
        placePopup!.remove()
      })
      m.on('click', layer, (e) => {
        const id = e.features && e.features[0] && (e.features[0].properties.id as number)
        if (id != null && placeClickCb) placeClickCb(id)
      })
    }
  }

  function setRotationEnabled(on: boolean): void {
    if (!map) return
    const fns = on ? 'enable' : 'disable'
    map.dragRotate[fns]()
    map.touchPitch[fns]()
    if (on) map.touchZoomRotate.enableRotation()
    else map.touchZoomRotate.disableRotation()
  }

  // ── Public API ──

  function init(container: HTMLElement | string): void {
    map = new maplibregl.Map({
      container,
      style: BOOTSTRAP_STYLE,
      center: [-98, 39], // center of US
      zoom: 4,
      maxZoom: 20,
      maxPitch: 80,
      attributionControl: false,
    })
    map.addControl(
      new maplibregl.AttributionControl({
        compact: true,
        customAttribution: [VECTOR_ATTRIBUTION, TERRAIN_ATTRIBUTION],
      }),
    )
    // Lock to a flat, north-up view by default; the 3D toggle unlocks rotate + pitch.
    setRotationEnabled(false)
    map.on('styledata', reinstallOverlays)
    map.on('style.load', handleStyleLoad)
    wireRangeTooltip(map)
    wireDronePopup(map)
    wirePhonePopup(map)
    wirePlaceInteraction(map)
    wirePoisInteraction(map)
    // Dispatch on a microtask: MapLibre fires moveend *synchronously* for
    // no-op camera moves (e.g. re-fitting an unchanged bounds), which would
    // run subscribers inside whatever $effect triggered the move — a
    // subscriber's read-modify-write ($state rev bump) then registers as that
    // effect's own dependency and the flush loops until Svelte aborts it.
    map.on('moveend', () => queueMicrotask(() => moveEndCbs.forEach((cb) => cb())))
    map.on('movestart', (e) => {
      if ((e as { originalEvent?: Event }).originalEvent) userMoveCbs.forEach((cb) => cb())
    })
    wireLongPress(map)
    map.on('idle', idlePrefetch)
    applyBasemap(currentLayer)
  }

  // Idle-time raster prefetch: once the camera settles, warm the tiles the
  // user is likely to need next (parents for zoom-out, a ring for panning) —
  // tile math in prefetch.ts. Raster-only; each GET also lands in the Pi's
  // disk cache, so online browsing doubles as offline precache. The session
  // dedupe set keeps repeat idles at the same camera free.
  const prefetched = new Set<string>()
  function idlePrefetch(): void {
    if (!map || currentLayer === 'osm') return
    const cfg = TILE_LAYERS[currentLayer]
    const b = map.getBounds()
    // 256px raster sources load tiles one level above the display zoom
    // (rounded — MapLibre raster cover uses roundZoom), so key the parents
    // and ring to the zoom actually on screen, not the camera zoom.
    const tileZoom = Math.round(map.getZoom() + 1)
    const tiles = prefetchTiles(
      { west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() },
      tileZoom,
      { maxzoom: cfg.maxzoom },
    )
    if (prefetched.size > 4000) prefetched.clear()
    for (const t of tiles) {
      const key = `${currentLayer}/${t.z}/${t.x}/${t.y}`
      if (prefetched.has(key)) continue
      prefetched.add(key)
      void fetch(`/tiles/${key}.png`, { priority: 'low' }).catch(() => {})
    }
  }

  function setLayer(layer: string): void {
    if (layer === currentLayer || (layer !== 'osm' && !TILE_LAYERS[layer])) return
    currentLayer = layer
    if (map) applyBasemap(layer)
  }

  function setRefreshMode(enabled: boolean): void {
    currentRefresh = enabled
    // Refresh only applies to raster layers; the vector base is one immutable file.
    if (currentLayer !== 'osm' && map) {
      const src = map.getSource(currentLayer) as maplibregl.RasterTileSource | undefined
      if (src && src.setTiles) src.setTiles([rasterTileUrl(currentLayer, currentRefresh)])
      else applyBasemap(currentLayer)
    }
  }

  // The MapLibre map itself when the vector base is active, else null. Used by the
  // label/POI controls to drive the Protomaps style layers directly.
  function getVectorBase(): MlMap | null {
    return currentLayer === 'osm' && map ? map : null
  }

  function onVectorBase(cb: (map: MlMap) => void): void {
    onVectorBaseCb = cb
    if (currentLayer === 'osm' && map && map.isStyleLoaded()) cb(map)
  }

  // --- Label scaling (Drive readability) ---------------------------------
  // Multiplies every symbol layer's text-size on the vector base while a view
  // (Drive) wants larger labels; restore with setLabelScale(1). Raster labels
  // are baked pixels — no-op there. Survives style reloads via handleStyleLoad.
  let labelScale = 1
  let labelSizeOriginals: Map<string, number | unknown[]> | null = null

  // A zoom-driven interpolate can't be wrapped in ['*', …] (MapLibre requires
  // ['zoom'] at the top level of interpolate/step), so scale the *outputs* of
  // the expression forms the shipped style actually uses; anything else passes
  // through unscaled rather than risking an invalid expression.
  function scaleSizeValue(v: number | unknown[] | unknown, f: number): unknown {
    if (typeof v === 'number') return v * f
    if (!Array.isArray(v)) return v
    const op = v[0]
    if (op === 'interpolate') {
      return v.map((x, i) => (i >= 4 && i % 2 === 0 ? scaleSizeValue(x, f) : x))
    }
    if (op === 'step') {
      return v.map((x, i) => (i >= 2 && i % 2 === 0 ? scaleSizeValue(x, f) : x))
    }
    if (op === 'case') {
      return v.map((x, i) =>
        i > 0 && (i % 2 === 0 || i === v.length - 1) ? scaleSizeValue(x, f) : x,
      )
    }
    return v
  }

  function applyLabelScale(): void {
    const gl = getVectorBase()
    // Pre-style-load there's nothing to capture; handleStyleLoad re-runs this.
    if (!gl || !gl.isStyleLoaded() || (labelScale === 1 && !labelSizeOriginals)) return
    try {
      if (!labelSizeOriginals) {
        labelSizeOriginals = new Map()
        for (const layer of gl.getStyle().layers) {
          if (layer.type !== 'symbol') continue
          const ts = (layer as { layout?: Record<string, unknown> }).layout?.['text-size']
          if (ts != null) labelSizeOriginals.set(layer.id, ts as number | unknown[])
        }
      }
      for (const [id, original] of labelSizeOriginals) {
        gl.setLayoutProperty(
          id,
          'text-size',
          labelScale === 1 ? original : scaleSizeValue(original, labelScale),
        )
      }
      if (labelScale === 1) labelSizeOriginals = null
    } catch (e) {
      console.error('label scale:', e)
    }
  }

  function setLabelScale(factor: number): void {
    labelScale = factor
    applyLabelScale()
  }

  function showTrack(
    points: MapPoint[],
    { fitBounds = true, showEndpoints = false }: { fitBounds?: boolean; showEndpoints?: boolean } = {},
  ): void {
    clearMarkers(endpointMarkers)
    lastTrackPoints = points
    trackData = lineFC(points)
    setGeoJSON('track', trackData)
    stopFeatures = points.filter((p) => p.kind === 'stop').map(stopPointToFeature)
    setGeoJSON('stops', stopsFC())
    if (!points.length) return

    if (showEndpoints) {
      addMarker(endpointMarkers, dotElement(), points[0].lat, points[0].lon, 'center', 'Start: ' + fmtTime(points[0].timestamp))
      if (points.length > 1) {
        const last = points.at(-1)!
        addMarker(endpointMarkers, dotElement(), last.lat, last.lon, 'center', 'End: ' + fmtTime(last.timestamp))
      }
    }

    if (fitBounds) fitTo(points)
  }

  function clearTrack(): void {
    clearMarkers(endpointMarkers)
    lastTrackPoints = []
    trackData = emptyFC()
    setGeoJSON('track', trackData)
    stopFeatures = []
    setGeoJSON('stops', stopsFC())
  }

  function clearAnnotations(): void {
    clearMarkers(pinMarkers)
    rangeFeatures = []
    setGeoJSON('ann-range', rangeFC())
  }

  function addRangeOverlay(points: MapPoint[], name?: string): void {
    if (points.length < 2) return
    rangeFeatures.push({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: points.map((p) => [p.lon, p.lat]) },
      properties: { name: name || '' },
    })
    setGeoJSON('ann-range', rangeFC())
  }

  function addPinOverlay(lat: number, lon: number, name?: string): void {
    if (!map) return
    addMarker(pinMarkers, pinElement(), lat, lon, 'bottom', name || '')
  }

  // Replace the drone overlay with the given flights, as floating 3D tracks. Lines
  // with <2 abs_alt points are dropped inside the builder. overlay3d is lazily
  // registered (the drone toggle imports overlay3d.ts on first use), so ensure its
  // custom layer is installed before pushing data — installLayer is idempotent.
  function showDroneTracks(flights: DroneFlight[]): void {
    if (overlay3d && map) overlay3d.installLayer(map)
    overlay3d?.setLines('drone', (flights || []).map(droneToLine))
  }

  function clearDroneTracks(): void {
    if (dronePopup) dronePopup.remove()
    overlay3d?.clear('drone')
  }

  // Replace the phone-history overlay with prebuilt GeoJSON (built by phone.ts:
  // color-by-mode line runs + visit pins). Clearing is setPhoneData(empty, empty).
  function setPhoneData(pathFC: FeatureCollection, visitFC: FeatureCollection): void {
    phonePathData = pathFC
    phoneVisitData = visitFC
    if (!map) return
    setGeoJSON('phone-track', phonePathData)
    setGeoJSON('phone-visits', phoneVisitData)
    if (phoneVisitData.features.length === 0 && phonePopup) phonePopup.remove()
  }

  // Replace the places overlay with prebuilt pin GeoJSON (built by
  // places.ts). Clearing is setPlacesData(empty).
  function setPlacesData(fc: FeatureCollection): void {
    placeData = fc
    if (!map) return
    setGeoJSON('places', placeData)
    if (placeData.features.length === 0 && placePopup) placePopup.remove()
  }

  // Replace the search-results overlay (the Places view's pushed result set).
  // Clearing is setSearchResultsData(empty). Data is retained like the other
  // overlays, so it survives style swaps and route remounts.
  function setSearchResultsData(fc: FeatureCollection): void {
    searchData = fc
    if (!map) return
    setGeoJSON('search-results', searchData)
    if (searchData.features.length === 0 && placePopup) placePopup.remove()
  }

  /** Fit the camera to a set of coordinates (the search-results handoff). */
  function fitToCoords(coords: { lat: number; lon: number }[]): void {
    fitTo(coords)
  }

  /** Register the place-pin click handler (receives the place row id). */
  function onPlaceClick(cb: (id: number) => void): void {
    placeClickCb = cb
  }

  /** Register the basemap-mark click handler (receives the decoded tile hit). */
  function onBasemapPoiClick(cb: (hit: BasemapPoiHit) => void): void {
    basemapPoiClickCb = cb
  }

  // ── Hover-scrub ghost dot (time → space) ──

  function setGhost(lat: number, lon: number): void {
    if (!map) return
    if (!ghostMarker) {
      const el = document.createElement('div')
      el.className = 'map-ghost-dot'
      ghostMarker = new maplibregl.Marker({ element: el, anchor: 'center' })
        .setLngLat([lon, lat])
        .addTo(map)
    } else {
      ghostMarker.setLngLat([lon, lat])
    }
  }

  function clearGhost(): void {
    ghostMarker?.remove()
    ghostMarker = null
  }

  // ── Drive view: oriented puck + follow camera ──

  /** Place/update the oriented van puck. Rotation is map-aligned (heading stays
   * true under a course-up bearing); null heading points north. */
  function setPuck(lat: number, lon: number, headingDeg: number | null): void {
    if (!map) return
    if (!puckMarker) {
      puckMarker = new maplibregl.Marker({
        element: puckElement(),
        anchor: 'center',
        rotationAlignment: 'map',
        pitchAlignment: 'map',
      })
        .setLngLat([lon, lat])
        .addTo(map)
    } else {
      puckMarker.setLngLat([lon, lat])
    }
    puckMarker.setRotation(headingDeg ?? 0)
  }

  function clearPuck(): void {
    puckMarker?.remove()
    puckMarker = null
  }

  /** Place/update the Drive destination pin (another style-swap-surviving marker). */
  function setDestination(lat: number, lon: number): void {
    if (!map) return
    if (!destMarker) {
      const el = document.createElement('div')
      el.className = 'map-dest-pin'
      destMarker = new maplibregl.Marker({ element: el, anchor: 'bottom' })
        .setLngLat([lon, lat])
        .addTo(map)
    } else {
      destMarker.setLngLat([lon, lat])
    }
  }

  function clearDestination(): void {
    destMarker?.remove()
    destMarker = null
  }

  /** Subscribe to a long-press / right-click on the map (dropped-pin destinations). */
  function onLongPress(cb: (lat: number, lon: number) => void): void {
    longPressCbs.add(cb)
  }

  function offLongPress(cb: (lat: number, lon: number) => void): void {
    longPressCbs.delete(cb)
  }

  function wireLongPress(m: MlMap): void {
    let timer: ReturnType<typeof setTimeout> | null = null
    let startPoint: { x: number; y: number } | null = null
    const cancel = (): void => {
      if (timer) clearTimeout(timer)
      timer = null
      startPoint = null
    }
    m.on('touchstart', (e) => {
      if (e.originalEvent.touches.length !== 1) {
        cancel()
        return
      }
      cancel()
      startPoint = { x: e.point.x, y: e.point.y }
      const { lat, lng } = e.lngLat
      timer = setTimeout(() => {
        cancel()
        longPressCbs.forEach((cb) => cb(lat, lng))
      }, LONG_PRESS_MS)
    })
    m.on('touchmove', (e) => {
      if (!startPoint) return
      const dx = e.point.x - startPoint.x
      const dy = e.point.y - startPoint.y
      if (dx * dx + dy * dy > LONG_PRESS_SLOP_PX ** 2) cancel()
    })
    m.on('touchend', cancel)
    m.on('touchcancel', cancel)
    m.on('contextmenu', (e) => {
      if (!longPressCbs.size) return
      e.preventDefault()
      longPressCbs.forEach((cb) => cb(e.lngLat.lat, e.lngLat.lng))
    })
  }

  /** Replace the Drive breadcrumb polyline (pass [] to clear). */
  function setBreadcrumb(points: { lat: number; lon: number }[]): void {
    breadcrumbData =
      points.length < 2
        ? emptyFC()
        : {
            type: 'FeatureCollection',
            features: [
              {
                type: 'Feature',
                geometry: {
                  type: 'LineString',
                  coordinates: points.map((p) => [p.lon, p.lat]),
                },
                properties: {},
              },
            ],
          }
    setGeoJSON('breadcrumb', breadcrumbData)
  }

  function clearBreadcrumb(): void {
    setBreadcrumb([])
  }

  /** Hard-set the camera — the follow loop's per-frame move. No easing: the
   * live store already interpolates, and stacked easeTo calls fight each other. */
  function setCamera(pose: CameraPose): void {
    if (!map) return
    map.jumpTo({
      center: [pose.lon, pose.lat],
      bearing: pose.bearing,
      pitch: pose.pitch,
      zoom: pose.zoom,
    })
  }

  /** Ease the camera toward a (partial) pose — view enter/leave transitions. */
  function easeCamera(pose: Partial<CameraPose> & { duration?: number }): void {
    if (!map) return
    map.easeTo({
      ...(pose.lat != null && pose.lon != null ? { center: [pose.lon, pose.lat] as [number, number] } : {}),
      ...(pose.bearing != null ? { bearing: pose.bearing } : {}),
      ...(pose.pitch != null ? { pitch: pose.pitch } : {}),
      ...(pose.zoom != null ? { zoom: pose.zoom } : {}),
      duration: pose.duration ?? 600,
    })
  }

  /** Subscribe to user-initiated camera moves (gesture-suspend for follow). */
  function onUserMove(cb: () => void): void {
    userMoveCbs.add(cb)
  }

  function offUserMove(cb: () => void): void {
    userMoveCbs.delete(cb)
  }

  /** The current viewport as an API bbox string (W,S,E,N — the places sync reads it); null pre-init. */
  function getBbox(): string | null {
    if (!map) return null
    const b = map.getBounds()
    return `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`
  }

  /** The current zoom level; 0 pre-init. */
  function getZoom(): number {
    return map ? map.getZoom() : 0
  }

  /** The current view center; null pre-init. */
  function getCenter(): { lat: number; lon: number } | null {
    if (!map) return null
    const c = map.getCenter()
    return { lat: c.lat, lon: c.lng }
  }

  function onMoveEnd(cb: () => void): void {
    moveEndCbs.add(cb)
  }

  function offMoveEnd(cb: () => void): void {
    moveEndCbs.delete(cb)
  }

  function fitTo(points: { lat: number; lon: number }[]): void {
    if (!map || !points.length) return
    const b = new maplibregl.LngLatBounds()
    for (const p of points) b.extend([p.lon, p.lat])
    if (!b.isEmpty()) map.fitBounds(b, { padding: 24 })
  }

  function fitToTrack(): void {
    fitTo(lastTrackPoints)
  }

  function zoomTo(lat: number, lon: number, zoom = 17): void {
    if (map) map.easeTo({ center: [lon, lat], zoom, duration: 600 })
  }

  function invalidateSize(): void {
    if (map) map.resize()
  }

  // ── 3D / terrain controls ──

  function setTerrainEnabled(enabled: boolean): void {
    terrainEnabled = enabled
    if (!map) return
    setRotationEnabled(enabled)
    applyTerrain()
    // Sync the floating overlay to the current exaggeration so drone tracks match
    // the DEM the moment 3D turns on.
    overlay3d?.setExaggeration(exaggeration)
    // Turning 3D off re-flattens to the north-up 2D view.
    map.easeTo({ pitch: enabled ? 60 : 0, bearing: enabled ? map.getBearing() : 0, duration: 600 })
  }

  function setExaggeration(value: number): void {
    exaggeration = value
    if (map && terrainEnabled) applyTerrain()
    overlay3d?.setExaggeration(value)
  }

  function getTerrainEnabled(): boolean {
    return terrainEnabled
  }

  function getLayer(): string {
    return currentLayer
  }

  function getExaggeration(): number {
    return exaggeration
  }

  function isInitialized(): boolean {
    return map !== undefined
  }

  return {
    init,
    isInitialized,
    showTrack,
    clearTrack,
    fitToTrack,
    zoomTo,
    invalidateSize,
    setRefreshMode,
    setLayer,
    setVectorTheme,
    getVectorBase,
    onVectorBase,
    setLabelScale,
    clearAnnotations,
    addRangeOverlay,
    addPinOverlay,
    showDroneTracks,
    clearDroneTracks,
    setPhoneData,
    setPlacesData,
    setSearchResultsData,
    fitToCoords,
    onPlaceClick,
    onBasemapPoiClick,
    setGhost,
    clearGhost,
    setPuck,
    clearPuck,
    setDestination,
    clearDestination,
    onLongPress,
    offLongPress,
    setBreadcrumb,
    clearBreadcrumb,
    setCamera,
    easeCamera,
    onUserMove,
    offUserMove,
    getBbox,
    getZoom,
    getCenter,
    onMoveEnd,
    offMoveEnd,
    setTerrainEnabled,
    setExaggeration,
    getTerrainEnabled,
    getLayer,
    getExaggeration,
  }
})()
