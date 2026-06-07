document.addEventListener('DOMContentLoaded', () => {
  MapView.init('map');
  TripsMap.init('trips-map');
  LabelControls.init([MapView, TripsMap]);

  Timeline.init();
  Trips.init();

  // Tab switching
  document.querySelectorAll('#tab-bar button').forEach(btn => {
    btn.addEventListener('click', () => {
      const view = btn.dataset.view;
      document.querySelectorAll('#tab-bar button').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(`view-${view}`).classList.add('active');
      MapView.invalidateSize();
      TripsMap.invalidateSize();
    });
  });

  // Tile refresh toggle
  const refreshToggle = document.getElementById('tile-refresh-toggle');
  const refreshBanner = document.getElementById('tile-refresh-banner');
  refreshToggle.addEventListener('change', () => {
    const enabled = refreshToggle.checked;
    MapView.setRefreshMode(enabled);
    TripsMap.setRefreshMode(enabled);
    refreshBanner.classList.toggle('hidden', !enabled);
  });

  // Tile layer selector. Refresh is meaningless for the vector OSM basemap (a
  // single immutable file), so disable the ↻ toggle while it's selected.
  const layerSelect = document.getElementById('layer-select');
  const syncLayerUi = (layer) => {
    const isVector = layer === 'osm';
    refreshToggle.disabled = isVector;
    if (isVector && refreshToggle.checked) {
      refreshToggle.checked = false;
      MapView.setRefreshMode(false);
      TripsMap.setRefreshMode(false);
      refreshBanner.classList.add('hidden');
    }
    LabelControls.setEnabled(isVector);
  };
  layerSelect.addEventListener('change', () => {
    MapView.setLayer(layerSelect.value);
    TripsMap.setLayer(layerSelect.value);
    syncLayerUi(layerSelect.value);
  });
  syncLayerUi(layerSelect.value); // default layer is vector OSM
});
