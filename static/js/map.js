// Map façade. One maplibregl.Map drives the whole view; this module is the only
// place that touches MapLibre directly. The public API (init, showTrack, …) is
// the contract timeline.js / annotations.js / app.js depend on — keep it stable.
//
// Basemaps swap via map.setStyle(); overlays (track, annotation ranges, terrain
// DEM sources, setTerrain) are re-installed on every style load by an idempotent
// handler, since setStyle drops everything not in the new style. Markers are DOM
// (maplibregl.Marker) and survive style swaps, so they're managed separately.

// Raster tile layers proxied/cached by Flask (mirrors the raster entries in
// api/tile_layers.py). The OSM basemap is vector (served at /tiles/osm.pmtiles),
// so USGS is the only raster layer left here.
const TILE_LAYERS = {
  usgs: {
    label: 'USGS Topo',
    attribution: '<a href="https://www.usgs.gov/">USGS</a> The National Map',
    maxzoom: 16,
  },
};

const VECTOR_ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, ' +
  '<a href="https://protomaps.com">Protomaps</a>';
const TERRAIN_ATTRIBUTION =
  '<a href="https://github.com/tilezen/joerd">Mapzen</a> / USGS NED (terrain)';

// Single immutable terrain DEM archive, byte-ranged by pmtiles.js. Same URL
// feeds two raster-dem sources (mesh + hillshade); sharing one source between
// setTerrain and a hillshade layer degrades rendering, so they stay separate.
const TERRAIN_PMTILES_URL = 'pmtiles://' + location.origin + '/tiles/terrain.pmtiles';

function rasterTileUrl(layer, refresh) {
  return `/tiles/${layer}/{z}/{x}/{y}.png` + (refresh ? '?refresh=1' : '');
}

// Register the pmtiles:// protocol once for the MapLibre instance.
const pmtilesProtocol = new pmtiles.Protocol();
maplibregl.addProtocol('pmtiles', pmtilesProtocol.tile);

// Fetch + patch the vector style once. MapLibre rejects a root-relative sprite
// URL, so sprite/glyphs are absolutized against location.origin (portable across
// localhost and the LAN IP); the pmtiles source URL stays root-relative.
let vectorStyle = null;
const vectorStyleReady = fetch('/static/vendor/basemap/style.json')
  .then(r => r.json())
  .then(s => {
    s.sprite = location.origin + s.sprite;
    s.glyphs = location.origin + s.glyphs;
    vectorStyle = s;
    return s;
  });

// Minimal style the map boots with so `map` exists synchronously; replaced by
// the real basemap as soon as its style is ready.
const BOOTSTRAP_STYLE = {
  version: 8,
  sources: {},
  layers: [{ id: 'bg', type: 'background', paint: { 'background-color': '#0f172a' } }],
};

function buildVectorStyle() {
  return JSON.parse(JSON.stringify(vectorStyle));
}

function buildRasterStyle(layer, refresh) {
  const cfg = TILE_LAYERS[layer];
  return {
    version: 8,
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
  };
}

const MapView = (() => {
  let map;
  let currentLayer = 'osm';
  let currentRefresh = false;
  let onVectorBaseCb = null;

  // 3D / terrain state (driven by the 3D toggle UI; default flat 2D).
  let terrainEnabled = false;
  let exaggeration = 1.3;

  // Overlay state, the source of truth re-applied after every style load.
  let trackData = emptyFC();
  let rangeFeatures = [];
  let lastTrackPoints = [];
  let endpointMarkers = [];
  let pinMarkers = [];

  let installing = false;     // re-entrancy guard for reinstallOverlays
  let rangePopup = null;

  const TRACK_COLOR = '#ef4444';
  const RANGE_COLOR = '#22d3ee';

  function emptyFC() {
    return { type: 'FeatureCollection', features: [] };
  }

  function lineFC(points) {
    if (points.length < 2) return emptyFC();
    return {
      type: 'FeatureCollection',
      features: [{
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: points.map(p => [p.lon, p.lat]) },
        properties: {},
      }],
    };
  }

  function rangeFC() {
    return { type: 'FeatureCollection', features: rangeFeatures };
  }

  // ── DOM markers (terrain-aware; clamp to ground automatically) ──

  function dotElement() {
    const el = document.createElement('div');
    el.style.cssText =
      'width:10px;height:10px;border-radius:50%;background:#3b82f6;' +
      'border:2px solid #fff;box-shadow:0 0 4px rgba(0,0,0,.5)';
    return el;
  }

  function pinElement() {
    const el = document.createElement('div');
    el.style.cssText =
      'width:14px;height:14px;border-radius:50% 50% 50% 0;background:#f59e0b;' +
      'border:2px solid #fff;transform:rotate(-45deg);box-shadow:0 0 4px rgba(0,0,0,.5)';
    return el;
  }

  function addMarker(list, element, lat, lon, anchor, title) {
    if (title) element.title = title;
    const m = new maplibregl.Marker({ element, anchor }).setLngLat([lon, lat]).addTo(map);
    list.push(m);
    return m;
  }

  function clearMarkers(list) {
    for (const m of list) m.remove();
    list.length = 0;
  }

  // ── Style / overlay lifecycle ──

  function demSource() {
    return {
      type: 'raster-dem',
      url: TERRAIN_PMTILES_URL,
      tileSize: 256,
      encoding: 'terrarium',
      maxzoom: 12,
    };
  }

  function firstSymbolLayerId() {
    const layers = map.getStyle().layers || [];
    const sym = layers.find(l => l.type === 'symbol');
    return sym ? sym.id : undefined;
  }

  // Add/remove the terrain mesh + (vector-only) hillshade to match the current
  // 3D state. Idempotent: only mutates when the desired state differs, so the
  // styledata churn it triggers doesn't loop.
  function applyTerrain() {
    const wantHillshade = terrainEnabled && currentLayer === 'osm';
    const hasHillshade = !!map.getLayer('hillshade');
    if (wantHillshade && !hasHillshade) {
      map.addLayer({
        id: 'hillshade',
        type: 'hillshade',
        source: 'hillshade-dem',
        paint: {
          'hillshade-exaggeration': 0.5,
          'hillshade-shadow-color': '#000',
          'hillshade-highlight-color': '#fff',
        },
      }, firstSymbolLayerId());
    } else if (!wantHillshade && hasHillshade) {
      map.removeLayer('hillshade');
    }

    const want = terrainEnabled ? { source: 'terrain-dem', exaggeration } : null;
    const cur = map.getTerrain();
    const same = (!want && !cur) ||
      (want && cur && cur.source === want.source && cur.exaggeration === want.exaggeration);
    if (!same) map.setTerrain(want);
  }

  // Re-add every overlay setStyle dropped. Idempotent (add-if-absent) and
  // re-entrancy-guarded, so it can run on every styledata event safely.
  function reinstallOverlays() {
    if (installing || !map.isStyleLoaded()) return;
    installing = true;
    try {
      if (!map.getSource('terrain-dem')) map.addSource('terrain-dem', demSource());
      if (!map.getSource('hillshade-dem')) map.addSource('hillshade-dem', demSource());

      if (!map.getSource('ann-range')) map.addSource('ann-range', { type: 'geojson', data: rangeFC() });
      if (!map.getLayer('ann-range-line')) {
        map.addLayer({
          id: 'ann-range-line',
          type: 'line',
          source: 'ann-range',
          layout: { 'line-join': 'round', 'line-cap': 'round' },
          paint: { 'line-color': RANGE_COLOR, 'line-width': 6, 'line-opacity': 0.7 },
        });
      }
      if (!map.getSource('track')) map.addSource('track', { type: 'geojson', data: trackData });
      if (!map.getLayer('track-line')) {
        map.addLayer({
          id: 'track-line',
          type: 'line',
          source: 'track',
          layout: { 'line-join': 'round', 'line-cap': 'round' },
          paint: { 'line-color': TRACK_COLOR, 'line-width': 3, 'line-opacity': 0.85 },
        });
      }

      // Sources are fresh after a style swap — push the current data back in.
      map.getSource('track').setData(trackData);
      map.getSource('ann-range').setData(rangeFC());

      applyTerrain();
    } finally {
      installing = false;
    }
  }

  function handleStyleLoad() {
    reinstallOverlays();
    if (currentLayer === 'osm' && onVectorBaseCb) onVectorBaseCb(map);
  }

  function applyBasemap(layer) {
    if (layer === 'osm') {
      vectorStyleReady.then(() => {
        if (currentLayer === 'osm') map.setStyle(buildVectorStyle());
      });
    } else {
      map.setStyle(buildRasterStyle(layer, currentRefresh));
    }
  }

  // Range-name tooltip on hover (parity with the old bindTooltip). Registered
  // once; the layer-scoped handler is a no-op until the layer exists.
  function wireRangeTooltip() {
    rangePopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
    map.on('mouseenter', 'ann-range-line', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mousemove', 'ann-range-line', (e) => {
      const name = e.features && e.features[0] && e.features[0].properties.name;
      if (name) rangePopup.setLngLat(e.lngLat).setText(name).addTo(map);
    });
    map.on('mouseleave', 'ann-range-line', () => {
      map.getCanvas().style.cursor = '';
      rangePopup.remove();
    });
  }

  // ── Public API ──

  function init(elementId) {
    map = new maplibregl.Map({
      container: elementId,
      style: BOOTSTRAP_STYLE,
      center: [-98, 39], // center of US
      zoom: 4,
      maxZoom: 20,
      maxPitch: 80,
      attributionControl: false,
    });
    map.addControl(new maplibregl.AttributionControl({
      compact: true,
      customAttribution: [VECTOR_ATTRIBUTION, TERRAIN_ATTRIBUTION],
    }));
    map.on('styledata', reinstallOverlays);
    map.on('style.load', handleStyleLoad);
    wireRangeTooltip();
    applyBasemap(currentLayer);
  }

  function setLayer(layer) {
    if (layer === currentLayer || (layer !== 'osm' && !TILE_LAYERS[layer])) return;
    currentLayer = layer;
    if (map) applyBasemap(layer);
  }

  function setRefreshMode(enabled) {
    currentRefresh = enabled;
    // Refresh only applies to raster layers; the vector base is a single
    // immutable file with no per-tile upstream to re-check.
    if (currentLayer !== 'osm' && map) {
      const src = map.getSource(currentLayer);
      if (src && src.setTiles) src.setTiles([rasterTileUrl(currentLayer, currentRefresh)]);
      else applyBasemap(currentLayer);
    }
  }

  // The MapLibre map itself when the vector base is active, else null. Used by
  // the label/POI controls to drive the Protomaps style layers directly.
  function getVectorBase() {
    return currentLayer === 'osm' && map ? map : null;
  }

  function onVectorBase(cb) {
    onVectorBaseCb = cb;
    if (currentLayer === 'osm' && map && map.isStyleLoaded()) cb(map);
  }

  function showTrack(points, { fitBounds = true, showEndpoints = false } = {}) {
    clearMarkers(endpointMarkers);
    lastTrackPoints = points;
    trackData = lineFC(points);
    if (map && map.getSource('track')) map.getSource('track').setData(trackData);
    if (!points.length) return;

    if (showEndpoints) {
      addMarker(endpointMarkers, dotElement(), points[0].lat, points[0].lon, 'center',
        'Start: ' + fmtTime(points[0].timestamp));
      if (points.length > 1) {
        const last = points.at(-1);
        addMarker(endpointMarkers, dotElement(), last.lat, last.lon, 'center',
          'End: ' + fmtTime(last.timestamp));
      }
    }

    if (fitBounds) fitTo(points);
  }

  function clearTrack() {
    clearMarkers(endpointMarkers);
    lastTrackPoints = [];
    trackData = emptyFC();
    if (map && map.getSource('track')) map.getSource('track').setData(trackData);
  }

  function clearAnnotations() {
    clearMarkers(pinMarkers);
    rangeFeatures = [];
    if (map && map.getSource('ann-range')) map.getSource('ann-range').setData(rangeFC());
  }

  function addRangeOverlay(points, name) {
    if (points.length < 2) return;
    rangeFeatures.push({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: points.map(p => [p.lon, p.lat]) },
      properties: { name: name || '' },
    });
    if (map && map.getSource('ann-range')) map.getSource('ann-range').setData(rangeFC());
  }

  function addPinOverlay(lat, lon, name) {
    if (!map) return;
    addMarker(pinMarkers, pinElement(), lat, lon, 'bottom', name || '');
  }

  function fitTo(points) {
    if (!map || !points.length) return;
    const b = new maplibregl.LngLatBounds();
    for (const p of points) b.extend([p.lon, p.lat]);
    if (!b.isEmpty()) map.fitBounds(b, { padding: 24 });
  }

  function fitToTrack() {
    fitTo(lastTrackPoints);
  }

  function zoomTo(lat, lon, zoom = 17) {
    if (map) map.easeTo({ center: [lon, lat], zoom, duration: 600 });
  }

  function invalidateSize() {
    if (map) map.resize();
  }

  // ── 3D / terrain controls (driven by the Phase 6 panel) ──

  function setTerrainEnabled(enabled) {
    terrainEnabled = enabled;
    if (!map) return;
    applyTerrain();
    map.easeTo({ pitch: enabled ? 60 : 0, bearing: enabled ? map.getBearing() : 0, duration: 600 });
  }

  function setExaggeration(value) {
    exaggeration = value;
    if (map && terrainEnabled) applyTerrain();
  }

  function getTerrainEnabled() {
    return terrainEnabled;
  }

  return {
    init, showTrack, clearTrack, fitToTrack, zoomTo, invalidateSize,
    setRefreshMode, setLayer, getVectorBase, onVectorBase,
    clearAnnotations, addRangeOverlay, addPinOverlay,
    setTerrainEnabled, setExaggeration, getTerrainEnabled,
  };
})();
