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
  let stopFeatures = [];
  let lastTrackPoints = [];
  let endpointMarkers = [];
  let pinMarkers = [];

  let installing = false;     // re-entrancy guard for reinstallOverlays
  let rangePopup = null;
  let dronePopup = null;

  // Drone overlay state, re-applied after every style load like the track/range.
  let droneFeatures = [];

  const TRACK_COLOR = '#ef4444';
  const RANGE_COLOR = '#22d3ee';

  // Per-model drone-track colors, keyed by the FC#### model_code. Keep in sync
  // with the legend swatches in templates/index.html.
  const DRONE_COLORS = {
    FC9313: '#a855f7', // DJI Mini 5 Pro — purple
    FC8485: '#f97316', // DJI Avata 2 — orange
    FC8671: '#ec4899', // DJI Neo — pink
  };
  const DRONE_DEFAULT_COLOR = '#94a3b8';

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

  function stopsFC() {
    return { type: 'FeatureCollection', features: stopFeatures };
  }

  // A stop → a Point feature carrying dwell minutes, used to scale the circle
  // radius. lineFC already routes the trail polyline through the stop centroid;
  // this just marks the pause node on top of it.
  function stopPointToFeature(p) {
    const dwellMin = (p.dwell_start && p.dwell_end)
      ? (new Date(p.dwell_end) - new Date(p.dwell_start)) / 60000 : 0;
    return {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
      properties: { dwell_min: dwellMin, n_raw: p.n_raw || 0 },
    };
  }

  function droneFC() {
    return { type: 'FeatureCollection', features: droneFeatures };
  }

  function droneColor(modelCode) {
    return DRONE_COLORS[modelCode] || DRONE_DEFAULT_COLOR;
  }

  function escapeHtml(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // One LineString feature per flight; abs_alt min/max are folded into properties
  // here (popup-only — the line itself drapes flat onto the terrain in v1). The
  // model_code property drives the per-model line color via a match expression.
  function droneFlightToFeature(flight) {
    const pts = flight.points || [];
    let altMin = null;
    let altMax = null;
    for (const p of pts) {
      if (p.abs_alt == null) continue;
      if (altMin == null || p.abs_alt < altMin) altMin = p.abs_alt;
      if (altMax == null || p.abs_alt > altMax) altMax = p.abs_alt;
    }
    return {
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: pts.map(p => [p.lon, p.lat]) },
      properties: {
        flight_id: flight.id,
        model: flight.model,
        model_code: flight.model_code,
        media_path: flight.media_path || '',
        source_name: flight.source_name || '',
        first_fix_utc: flight.first_fix_utc,
        last_fix_utc: flight.last_fix_utc,
        n_points: flight.n_points,
        alt_min: altMin,
        alt_max: altMax,
      },
    };
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
  //
  // Driven off styledata (not gated on isStyleLoaded): adding sources/layers
  // only needs the style *document*, which is present once setStyle resolves —
  // isStyleLoaded() additionally waits for source tile data, which for the
  // byte-ranged vector pmtiles source can lag well past style.load, and gating
  // on it dropped the overlays entirely on the vector basemap. The try/catch
  // tolerates a too-early call; the next styledata retries idempotently.
  function reinstallOverlays() {
    if (installing) return;
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

      // Stops as a constant-size dot on the trail — a "you stopped here" node, not
      // scaled by dwell. Dwell duration reads off the timeline block instead (its
      // width + hover label), so the map only answers *where*. The white stroke
      // separates the dot from the same-colored track line (mapview-redesign S3).
      if (!map.getSource('stops')) map.addSource('stops', { type: 'geojson', data: stopsFC() });
      if (!map.getLayer('stop-circle')) {
        map.addLayer({
          id: 'stop-circle',
          type: 'circle',
          source: 'stops',
          paint: {
            'circle-radius': 5,
            'circle-color': TRACK_COLOR,
            'circle-stroke-color': '#fff',
            'circle-stroke-width': 1.5,
          },
        });
      }

      // Drone tracks sit above the van track/ranges. One source, per-model color
      // via a match on model_code (default for any unknown FC#### code).
      if (!map.getSource('drone-tracks')) map.addSource('drone-tracks', { type: 'geojson', data: droneFC() });
      if (!map.getLayer('drone-line')) {
        map.addLayer({
          id: 'drone-line',
          type: 'line',
          source: 'drone-tracks',
          layout: { 'line-join': 'round', 'line-cap': 'round' },
          paint: {
            'line-color': [
              'match', ['get', 'model_code'],
              'FC9313', DRONE_COLORS.FC9313,
              'FC8485', DRONE_COLORS.FC8485,
              'FC8671', DRONE_COLORS.FC8671,
              DRONE_DEFAULT_COLOR,
            ],
            'line-width': 2.5,
            'line-opacity': 0.9,
          },
        });
      }

      // Sources are fresh after a style swap — push the current data back in.
      const track = map.getSource('track');
      if (track) track.setData(trackData);
      const stops = map.getSource('stops');
      if (stops) stops.setData(stopsFC());
      const range = map.getSource('ann-range');
      if (range) range.setData(rangeFC());
      const drone = map.getSource('drone-tracks');
      if (drone) drone.setData(droneFC());

      applyTerrain();
    } catch (e) {
      console.error('reinstallOverlays:', e);
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

  // Click a drone track → a popup with model, time span, altitude range, and the
  // canonical media path. Registered once; the layer-scoped handlers no-op until
  // the drone-line layer exists.
  function dronePopupHtml(p) {
    const durMs = new Date(p.last_fix_utc) - new Date(p.first_fix_utc);
    const alt = (p.alt_min != null && p.alt_max != null)
      ? `${fmtAltitude(p.alt_min)}–${fmtAltitude(p.alt_max)} MSL`
      : '—';
    const path = p.media_path
      ? `<div class="drone-popup-path" title="${escapeHtml(p.media_path)}">${escapeHtml(p.media_path)}</div>`
      : `<div class="drone-popup-path muted">${escapeHtml(p.source_name || 'no media path')}</div>`;
    return (
      `<div class="drone-popup">` +
      `<div class="drone-popup-title">` +
      `<span class="drone-popup-swatch" style="background:${droneColor(p.model_code)}"></span>` +
      `${escapeHtml(p.model)}</div>` +
      `<div class="drone-popup-meta">${fmtDate(p.first_fix_utc)} · ${fmtTime(p.first_fix_utc)} → ${fmtTime(p.last_fix_utc)} · ${fmtDuration(durMs)}</div>` +
      `<div class="drone-popup-meta">Alt ${alt} · ${p.n_points} pts</div>` +
      path +
      `</div>`
    );
  }

  function wireDronePopup() {
    dronePopup = new maplibregl.Popup({ closeButton: true, closeOnClick: true, maxWidth: '300px' });
    map.on('mouseenter', 'drone-line', () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', 'drone-line', () => { map.getCanvas().style.cursor = ''; });
    map.on('click', 'drone-line', (e) => {
      const f = e.features && e.features[0];
      if (!f) return;
      dronePopup.setLngLat(e.lngLat).setHTML(dronePopupHtml(f.properties)).addTo(map);
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
    // Lock to a flat, north-up view by default. The 3D toggle unlocks rotate +
    // pitch (setTerrainEnabled).
    setRotationEnabled(false);
    map.on('styledata', reinstallOverlays);
    map.on('style.load', handleStyleLoad);
    wireRangeTooltip();
    wireDronePopup();
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
    stopFeatures = points.filter(p => p.kind === 'stop').map(stopPointToFeature);
    if (map && map.getSource('stops')) map.getSource('stops').setData(stopsFC());
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
    stopFeatures = [];
    if (map && map.getSource('stops')) map.getSource('stops').setData(stopsFC());
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

  // Replace the drone overlay with the given flights (≥2-point tracks only — a
  // LineString needs two coords; degenerate single-fix flights are dropped). The
  // features are retained so reinstallOverlays can re-push them after a style swap.
  function showDroneTracks(flights) {
    droneFeatures = (flights || [])
      .filter(f => (f.points || []).length >= 2)
      .map(droneFlightToFeature);
    if (map && map.getSource('drone-tracks')) map.getSource('drone-tracks').setData(droneFC());
  }

  function clearDroneTracks() {
    droneFeatures = [];
    if (dronePopup) dronePopup.remove();
    if (map && map.getSource('drone-tracks')) map.getSource('drone-tracks').setData(droneFC());
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

  function setRotationEnabled(on) {
    const fns = on ? 'enable' : 'disable';
    map.dragRotate[fns]();
    map.touchPitch[fns]();
    if (on) map.touchZoomRotate.enableRotation();
    else map.touchZoomRotate.disableRotation();
  }

  function setTerrainEnabled(enabled) {
    terrainEnabled = enabled;
    if (!map) return;
    setRotationEnabled(enabled);
    applyTerrain();
    // Turning 3D off re-flattens to the north-up 2D view.
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
    showDroneTracks, clearDroneTracks,
    setTerrainEnabled, setExaggeration, getTerrainEnabled,
  };
})();
