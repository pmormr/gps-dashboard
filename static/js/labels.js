// Label / POI density controls for the vector basemap. Drives the inner
// MapLibre GL map of each registered map controller (timeline + annotations) via the
// maplibre-gl-leaflet plugin's getMaplibreMap(). Settings are global: one panel
// applies to every vector base, re-applied whenever a GL style (re)loads.
const LabelControls = (() => {
  // POI category -> the `kind` values it surfaces in the Protomaps schema.
  const GROUPS = {
    Fuel: ['fuel'],
    Lodging: ['hotel', 'motel', 'hostel', 'guest_house'],
    Food: ['restaurant', 'cafe', 'fast_food', 'bar', 'pub', 'brewery'],
    Medical: ['hospital', 'clinic', 'doctors', 'pharmacy', 'dentist'],
    Parking: ['parking'],
    Shops: ['supermarket', 'convenience', 'mall', 'marketplace'],
    Outdoors: ['park', 'peak', 'viewpoint', 'camp_site', 'picnic_site', 'beach', 'information'],
    Civic: ['library', 'museum', 'post_office', 'townhall', 'school',
            'university', 'college', 'place_of_worship', 'theatre', 'bank'],
  };

  const enabled = new Set(Object.keys(GROUPS)); // all on by default
  let offset = -1;        // min-zoom offset: lower = labels appear earlier/denser
  let minorRoads = true;  // minor street names at z13+ vs hidden
  let controllers = [];

  // Push the current settings into one GL map. setFilter widens the pois layer
  // to the surfaced kinds and gates them by the density offset; setLayerZoomRange
  // controls minor road labels. The shipped style's pois `text-color` is a case
  // on `kind` with no branch for the kinds we surface (fuel, hospital, …), so
  // they fall to the #e2dfda fallback — identical to text-halo-color, i.e.
  // invisible. Recolor only that fallback to a readable dark; covered kinds keep
  // their category colors.
  function applyToGl(gl) {
    if (!gl || !gl.isStyleLoaded()) return;
    const kinds = new Set();
    for (const g of enabled) GROUPS[g].forEach(k => kinds.add(k));
    try {
      gl.setFilter('pois', ['all',
        ['in', ['get', 'kind'], ['literal', [...kinds]]],
        ['>=', ['zoom'], ['+', ['get', 'min_zoom'], offset]],
      ]);
      gl.setLayerZoomRange('roads_labels_minor', minorRoads ? 13 : 24, 24);
      const tc = gl.getPaintProperty('pois', 'text-color');
      if (Array.isArray(tc) && tc[tc.length - 1] !== '#3a3a3a') {
        const fixed = tc.slice();
        fixed[fixed.length - 1] = '#3a3a3a';
        gl.setPaintProperty('pois', 'text-color', fixed);
      }
    } catch (e) {
      console.error('label controls:', e);
    }
  }

  function applyAll() {
    for (const c of controllers) {
      const base = c.getVectorBase();
      if (base) applyToGl(base.getMaplibreMap());
    }
  }

  // (Re)attach to a freshly created vector base. styledata fires on every style
  // (re)load — including after a base-layer swap — so settings survive swaps.
  function hookBase(base) {
    const gl = base.getMaplibreMap();
    if (!gl) return;
    gl.off('styledata', applyAll);
    gl.on('styledata', applyAll);
    applyToGl(gl);
  }

  function buildPanel() {
    const cats = document.getElementById('label-cats');
    for (const g of Object.keys(GROUPS)) {
      const lab = document.createElement('label');
      lab.className = 'label-check';
      lab.innerHTML = `<input type="checkbox" checked data-group="${g}"> ${g}`;
      lab.querySelector('input').addEventListener('change', (ev) => {
        ev.target.checked ? enabled.add(g) : enabled.delete(g);
        applyAll();
      });
      cats.appendChild(lab);
    }

    const offInput = document.getElementById('label-off');
    const offVal = document.getElementById('label-offv');
    offInput.addEventListener('input', (ev) => {
      offset = +ev.target.value;
      offVal.textContent = offset;
      applyAll();
    });

    document.getElementById('label-minor').addEventListener('change', (ev) => {
      minorRoads = ev.target.checked;
      applyAll();
    });

    const panel = document.getElementById('label-panel');
    document.getElementById('label-panel-toggle').addEventListener('click', () => {
      panel.classList.toggle('collapsed');
    });
  }

  function init(ctrls) {
    controllers = ctrls;
    buildPanel();
    for (const c of controllers) c.onVectorBase(hookBase);
  }

  // Show the panel only for the vector basemap; raster layers have no labels.
  function setEnabled(isVector) {
    document.getElementById('label-panel').classList.toggle('hidden', !isVector);
  }

  return { init, setEnabled };
})();
