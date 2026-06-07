// Raster tile layers proxied/cached by Flask (mirrors the raster entries in
// api/tile_layers.py). The OSM basemap is now vector (MapLibre GL + PMTiles,
// served at /tiles/osm.pmtiles), so USGS is the only raster layer left here.
// `maxNativeZoom` lets Leaflet upsample the deepest available tile rather than
// asking for ones the upstream doesn't serve.
const TILE_LAYERS = {
  usgs: {
    label: 'USGS Topo',
    attribution: '<a href="https://www.usgs.gov/">USGS</a> The National Map',
    maxNativeZoom: 16,
  },
};

const VECTOR_ATTRIBUTION =
  '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, ' +
  '<a href="https://protomaps.com">Protomaps</a>';

function tileUrl(layer, refresh) {
  return `/tiles/${layer}/{z}/{x}/{y}.png` + (refresh ? '?refresh=1' : '');
}

function makeTileLayer(layer, refresh) {
  const cfg = TILE_LAYERS[layer];
  return L.tileLayer(tileUrl(layer, refresh), {
    attribution: cfg.attribution,
    maxNativeZoom: cfg.maxNativeZoom,
    maxZoom: 20,
    errorTileUrl: '/static/img/tile-error.png',
  });
}

// Register the pmtiles:// protocol once for every MapLibre GL instance.
const pmtilesProtocol = new pmtiles.Protocol();
maplibregl.addProtocol('pmtiles', pmtilesProtocol.tile);

// Fetch + patch the vector style once, then clone it per map. MapLibre rejects
// a root-relative sprite URL, so sprite/glyphs are absolutized against
// location.origin (portable across localhost and the LAN IP); the pmtiles
// source URL stays root-relative.
let vectorStyle = null;
const vectorStyleReady = fetch('/static/vendor/basemap/style.json')
  .then(r => r.json())
  .then(s => {
    s.sprite = location.origin + s.sprite;
    s.glyphs = location.origin + s.glyphs;
    vectorStyle = s;
    return s;
  });

// A fresh GL base per call — each Leaflet map needs its own, and cloning keeps
// per-map style edits (label/POI controls) from bleeding across maps.
function makeVectorBase() {
  const style = JSON.parse(JSON.stringify(vectorStyle));
  return L.maplibreGL({ style, attribution: VECTOR_ATTRIBUTION });
}

const MapView = (() => {
  let map, baseLayer, trackLayer, markerLayer;
  let currentLayer = 'osm';
  let currentRefresh = false;

  const trackStyle = { color: '#ef4444', weight: 3, opacity: 0.85 };

  const dotIcon = L.divIcon({
    className: '',
    html: '<div style="width:10px;height:10px;border-radius:50%;background:#3b82f6;border:2px solid #fff;box-shadow:0 0 4px rgba(0,0,0,.5)"></div>',
    iconSize: [10, 10],
    iconAnchor: [5, 5],
  });

  // Swap the base layer. 'osm' is the vector GL base (added once its style
  // resolves); everything else is a raster proxy layer. Overlays live in their
  // own (higher) Leaflet panes, so base add/remove never disturbs the track.
  function setBase(layer) {
    if (baseLayer) { map.removeLayer(baseLayer); baseLayer = null; }
    if (layer === 'osm') {
      vectorStyleReady.then(() => {
        if (currentLayer !== 'osm' || baseLayer) return;
        baseLayer = makeVectorBase().addTo(map);
      });
    } else {
      baseLayer = makeTileLayer(layer, currentRefresh).addTo(map);
    }
  }

  function init(elementId) {
    map = L.map(elementId, { zoomControl: true, maxZoom: 20 });
    trackLayer = L.layerGroup().addTo(map);
    markerLayer = L.layerGroup().addTo(map);
    setBase(currentLayer);
    map.setView([39, -98], 4); // default: center of US
  }

  function setLayer(layer) {
    if (layer === currentLayer || (layer !== 'osm' && !TILE_LAYERS[layer])) return;
    currentLayer = layer;
    if (map) setBase(layer);
  }

  function setRefreshMode(enabled) {
    currentRefresh = enabled;
    // Refresh only applies to raster layers; the vector base is a single
    // immutable file with no per-tile upstream to re-check.
    if (currentLayer !== 'osm' && baseLayer && baseLayer.setUrl) {
      baseLayer.setUrl(tileUrl(currentLayer, currentRefresh));
    }
  }

  // The inner MapLibre map of the vector base, or null when raster is active.
  // Used by the label/POI controls.
  function getVectorBase() {
    return currentLayer === 'osm' ? baseLayer : null;
  }

  function showTrack(points, { fitBounds = true, showEndpoints = false } = {}) {
    trackLayer.clearLayers();
    markerLayer.clearLayers();
    if (!points.length) return;

    const latlngs = points.map(p => [p.lat, p.lon]);
    const poly = L.polyline(latlngs, trackStyle).addTo(trackLayer);

    if (showEndpoints) {
      L.marker([points[0].lat, points[0].lon], { icon: dotIcon })
        .bindTooltip('Start: ' + fmtTime(points[0].timestamp))
        .addTo(markerLayer);
      if (points.length > 1) {
        L.marker([points.at(-1).lat, points.at(-1).lon], { icon: dotIcon })
          .bindTooltip('End: ' + fmtTime(points.at(-1).timestamp))
          .addTo(markerLayer);
      }
    }

    if (fitBounds) map.fitBounds(poly.getBounds(), { padding: [24, 24] });
  }

  function clearTrack() {
    trackLayer.clearLayers();
    markerLayer.clearLayers();
  }

  function fitToTrack() {
    const layers = trackLayer.getLayers();
    if (layers.length) map.fitBounds(layers[0].getBounds(), { padding: [24, 24] });
  }

  function zoomTo(lat, lon, zoom = 17) {
    if (map) map.setView([lat, lon], zoom);
  }

  function invalidateSize() {
    if (map) map.invalidateSize();
  }

  return { init, showTrack, clearTrack, fitToTrack, zoomTo, invalidateSize, setRefreshMode, setLayer, getVectorBase };
})();

// Second map instance for the Trips detail pane
const TripsMap = (() => {
  let map, baseLayer, trackLayer, markerLayer;
  let currentLayer = 'osm';
  let currentRefresh = false;

  const trackStyle = { color: '#ef4444', weight: 3, opacity: 0.85 };

  const dotIcon = L.divIcon({
    className: '',
    html: '<div style="width:10px;height:10px;border-radius:50%;background:#3b82f6;border:2px solid #fff;box-shadow:0 0 4px rgba(0,0,0,.5)"></div>',
    iconSize: [10, 10],
    iconAnchor: [5, 5],
  });

  function setBase(layer) {
    if (baseLayer) { map.removeLayer(baseLayer); baseLayer = null; }
    if (layer === 'osm') {
      vectorStyleReady.then(() => {
        if (currentLayer !== 'osm' || baseLayer) return;
        baseLayer = makeVectorBase().addTo(map);
      });
    } else {
      baseLayer = makeTileLayer(layer, currentRefresh).addTo(map);
    }
  }

  function init(elementId) {
    map = L.map(elementId, { zoomControl: true, maxZoom: 20 });
    trackLayer = L.layerGroup().addTo(map);
    markerLayer = L.layerGroup().addTo(map);
    setBase(currentLayer);
    map.setView([39, -98], 4);
  }

  function setLayer(layer) {
    if (layer === currentLayer || (layer !== 'osm' && !TILE_LAYERS[layer])) return;
    currentLayer = layer;
    if (map) setBase(layer);
  }

  function setRefreshMode(enabled) {
    currentRefresh = enabled;
    if (currentLayer !== 'osm' && baseLayer && baseLayer.setUrl) {
      baseLayer.setUrl(tileUrl(currentLayer, currentRefresh));
    }
  }

  function getVectorBase() {
    return currentLayer === 'osm' ? baseLayer : null;
  }

  function showTrack(points, { fitBounds = true, showEndpoints = false } = {}) {
    trackLayer.clearLayers();
    markerLayer.clearLayers();
    if (!points.length) return;

    const latlngs = points.map(p => [p.lat, p.lon]);
    const poly = L.polyline(latlngs, trackStyle).addTo(trackLayer);

    if (showEndpoints) {
      L.marker([points[0].lat, points[0].lon], { icon: dotIcon })
        .bindTooltip('Start: ' + fmtTime(points[0].timestamp))
        .addTo(markerLayer);
      if (points.length > 1) {
        L.marker([points.at(-1).lat, points.at(-1).lon], { icon: dotIcon })
          .bindTooltip('End: ' + fmtTime(points.at(-1).timestamp))
          .addTo(markerLayer);
      }
    }

    if (fitBounds) map.fitBounds(poly.getBounds(), { padding: [24, 24] });
  }

  function clearTrack() {
    if (trackLayer) trackLayer.clearLayers();
    if (markerLayer) markerLayer.clearLayers();
  }

  function invalidateSize() {
    if (map) map.invalidateSize();
  }

  return { init, showTrack, clearTrack, invalidateSize, setRefreshMode, setLayer, getVectorBase };
})();
