document.addEventListener('DOMContentLoaded', () => {
  MapView.init('map');
  LabelControls.init([MapView]);

  TimePicker.init();
  Timeline.init();
  Annotations.init();

  // Annotations drawer toggle (replaces the old Annotations tab)
  const drawer = document.getElementById('annotations-drawer');
  const drawerToggle = document.getElementById('annotations-drawer-toggle');
  const drawerClose = document.getElementById('annotations-drawer-close');

  function setDrawer(open) {
    drawer.classList.toggle('open', open);
    drawer.classList.toggle('hidden', !open);
    drawerToggle.classList.toggle('active', open);
    // Resize the map after a slide so Leaflet recomputes its container width.
    setTimeout(() => MapView.invalidateSize(), 250);
  }

  drawerToggle.addEventListener('click', () => {
    setDrawer(drawer.classList.contains('hidden'));
  });
  drawerClose.addEventListener('click', () => setDrawer(false));

  // Tile refresh toggle
  const refreshToggle = document.getElementById('tile-refresh-toggle');
  const refreshBanner = document.getElementById('tile-refresh-banner');
  refreshToggle.addEventListener('change', () => {
    const enabled = refreshToggle.checked;
    MapView.setRefreshMode(enabled);
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
      refreshBanner.classList.add('hidden');
    }
    LabelControls.setEnabled(isVector);
  };
  layerSelect.addEventListener('change', () => {
    MapView.setLayer(layerSelect.value);
    syncLayerUi(layerSelect.value);
  });
  syncLayerUi(layerSelect.value); // default layer is vector OSM

  // 3D terrain panel. The toggle drapes the active basemap on the terrain mesh
  // (works for both vector OSM and USGS); the exaggeration slider appears only
  // while 3D is on.
  const terrainPanel = document.getElementById('terrain-panel');
  document.getElementById('terrain-panel-toggle').addEventListener('click', () => {
    terrainPanel.classList.toggle('collapsed');
  });
  const terrain3dToggle = document.getElementById('terrain-3d-toggle');
  const terrainExagRow = document.getElementById('terrain-exag-row');
  const terrainExag = document.getElementById('terrain-exag');
  const terrainExagVal = document.getElementById('terrain-exagv');
  terrain3dToggle.addEventListener('change', () => {
    const on = terrain3dToggle.checked;
    MapView.setTerrainEnabled(on);
    terrainExagRow.classList.toggle('hidden', !on);
  });
  terrainExag.addEventListener('input', () => {
    terrainExagVal.textContent = terrainExag.value;
    MapView.setExaggeration(parseFloat(terrainExag.value));
  });
});
