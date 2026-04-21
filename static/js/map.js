const TILE_URL = '/tiles/{z}/{x}/{y}.png';
const TILE_URL_REFRESH = '/tiles/{z}/{x}/{y}.png?refresh=1';

const MapView = (() => {
  let map, tileLayer, trackLayer, markerLayer;

  const trackStyle = { color: '#ef4444', weight: 3, opacity: 0.85 };

  const dotIcon = L.divIcon({
    className: '',
    html: '<div style="width:10px;height:10px;border-radius:50%;background:#3b82f6;border:2px solid #fff;box-shadow:0 0 4px rgba(0,0,0,.5)"></div>',
    iconSize: [10, 10],
    iconAnchor: [5, 5],
  });

  function init(elementId) {
    map = L.map(elementId, { zoomControl: true });
    tileLayer = L.tileLayer(TILE_URL, {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
      errorTileUrl: '/static/img/tile-error.png',
    }).addTo(map);
    trackLayer = L.layerGroup().addTo(map);
    markerLayer = L.layerGroup().addTo(map);
    map.setView([39, -98], 4); // default: center of US
  }

  function setRefreshMode(enabled) {
    if (tileLayer) tileLayer.setUrl(enabled ? TILE_URL_REFRESH : TILE_URL);
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

  return { init, showTrack, clearTrack, fitToTrack, zoomTo, invalidateSize, setRefreshMode };
})();

// Second map instance for the Trips detail pane
const TripsMap = (() => {
  let map, tileLayer, trackLayer, markerLayer;

  const trackStyle = { color: '#ef4444', weight: 3, opacity: 0.85 };

  const dotIcon = L.divIcon({
    className: '',
    html: '<div style="width:10px;height:10px;border-radius:50%;background:#3b82f6;border:2px solid #fff;box-shadow:0 0 4px rgba(0,0,0,.5)"></div>',
    iconSize: [10, 10],
    iconAnchor: [5, 5],
  });

  function init(elementId) {
    map = L.map(elementId, { zoomControl: true });
    tileLayer = L.tileLayer(TILE_URL, {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
      errorTileUrl: '/static/img/tile-error.png',
    }).addTo(map);
    trackLayer = L.layerGroup().addTo(map);
    markerLayer = L.layerGroup().addTo(map);
    map.setView([39, -98], 4);
  }

  function setRefreshMode(enabled) {
    if (tileLayer) tileLayer.setUrl(enabled ? TILE_URL_REFRESH : TILE_URL);
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

  return { init, showTrack, clearTrack, invalidateSize, setRefreshMode };
})();
