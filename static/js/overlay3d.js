/**
 * Generic elevated-data overlay for the MapLibre map.
 *
 * MapLibre (v5.24) has no elevated-line support, so anything that must float above
 * the terrain at a true altitude is drawn by three.js instead. This module is a
 * single MapLibre custom 3D layer hosting one three.js scene, composited into
 * MapLibre's own camera/GL context — the map stays MapLibre; this only adds a 3D
 * data plane on top of it. Reuses the vendored three.js that /globe ships.
 *
 * Geometry is keyed by *group* so the layer is not tied to any one dataset: drones
 * are the first consumer (`setLines('drone', …)`); van tracks, sensor columns, etc.
 * drop in later as additional groups. Each vertex is placed with
 * `MercatorCoordinate.fromLngLat([lon, lat], altMeters)` — and because altitudes
 * here are MSL and the terrain DEM is MSL, points float at the correct height above
 * the rendered terrain for free.
 *
 * Only a polyline primitive exists today; add point/column/extrusion types when a
 * consumer needs one. Picking is left to MapLibre — three.js geometry isn't
 * queryable — so a consumer that needs clicks keeps a flat companion line layer.
 */
import * as THREE from 'three';

const Overlay3D = (() => {
  const LAYER_ID = 'overlay-3d';

  let map = null;
  let scene = null;
  let camera = null;
  let renderer = null;

  // groupId → { lines: Array<{coords:[lon,lat,alt][], color?}>, color?, opacity?,
  //             object: THREE.Group|null }. Data is retained across style swaps;
  //            `object` is the live scene node, rebuilt on each onAdd.
  const groups = new Map();

  /** [lon, lat, altMeters] → a MapLibre mercator-world coordinate (x,y in [0,1], z up). */
  function merc(lon, lat, alt) {
    return window.maplibregl.MercatorCoordinate.fromLngLat([lon, lat], alt || 0);
  }

  /** Free a THREE subtree's GPU resources. */
  function dispose(obj) {
    obj.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) o.material.dispose();
    });
  }

  /**
   * Build a THREE.Group of polylines for one data group. Each line sits at its own
   * first-vertex mercator origin with vertices stored *relative* to it, so the tiny
   * float32 deltas keep precision instead of riding on a ~0.25 absolute coordinate.
   */
  function buildGroup(g) {
    const root = new THREE.Group();
    for (const line of g.lines) {
      const pts = line.coords;
      if (!pts || pts.length < 2) continue;
      const o = merc(pts[0][0], pts[0][1], pts[0][2]);
      const pos = new Float32Array(pts.length * 3);
      for (let i = 0; i < pts.length; i++) {
        const m = merc(pts[i][0], pts[i][1], pts[i][2]);
        pos[i * 3] = m.x - o.x;
        pos[i * 3 + 1] = m.y - o.y;
        pos[i * 3 + 2] = m.z - o.z;
      }
      const geom = new THREE.BufferGeometry();
      geom.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      const obj = new THREE.Line(geom, new THREE.LineBasicMaterial({
        color: line.color || g.color || '#ffffff',
        transparent: true,
        opacity: line.opacity ?? g.opacity ?? 0.95,
      }));
      obj.position.set(o.x, o.y, o.z);
      // Mercator-space coords defeat three's frustum test; never cull.
      obj.frustumCulled = false;
      root.add(obj);
    }
    return root;
  }

  /** Rebuild one group's scene node from its retained line data. */
  function refresh(groupId) {
    const g = groups.get(groupId);
    if (!g || !scene) return;
    if (g.object) {
      scene.remove(g.object);
      dispose(g.object);
      g.object = null;
    }
    if (g.lines.length) {
      g.object = buildGroup(g);
      scene.add(g.object);
    }
    if (map) map.triggerRepaint();
  }

  // The MapLibre custom layer. setStyle drops it (calling onRemove); reinstallLayer
  // re-adds it and onAdd rebuilds the scene from the retained group data.
  const layer = {
    id: LAYER_ID,
    type: 'custom',
    renderingMode: '3d',
    onAdd(m, gl) {
      map = m;
      scene = new THREE.Scene();
      camera = new THREE.Camera();
      renderer = new THREE.WebGLRenderer({ canvas: m.getCanvas(), context: gl, antialias: true });
      renderer.autoClear = false;
      for (const id of groups.keys()) refresh(id);
    },
    render(_gl, args) {
      if (!renderer) return;
      // v5 globe-era custom-layer API: the mercator→clip matrix is
      // args.defaultProjectionData.mainMatrix (older builds passed a bare array).
      const m = args?.defaultProjectionData?.mainMatrix ?? args;
      camera.projectionMatrix.fromArray(m);
      renderer.resetState();
      renderer.render(scene, camera);
    },
    onRemove() {
      for (const g of groups.values()) {
        if (g.object) {
          dispose(g.object);
          g.object = null;
        }
      }
      renderer = null;
      scene = null;
      camera = null;
    },
  };

  /** Add the custom layer if absent. Safe to call on every styledata. */
  function installLayer(m) {
    if (!m.getLayer(LAYER_ID)) m.addLayer(layer);
  }

  /**
   * Replace a group's polylines. `lines` is [{coords:[[lon,lat,altMeters],…], color?}].
   * `opts.color` / `opts.opacity` are group-wide fallbacks a line can override.
   */
  function setLines(groupId, lines, opts = {}) {
    const g = groups.get(groupId) || { lines: [], object: null };
    g.lines = lines || [];
    if (opts.color != null) g.color = opts.color;
    if (opts.opacity != null) g.opacity = opts.opacity;
    groups.set(groupId, g);
    refresh(groupId);
  }

  /** Remove a group's polylines. */
  function clear(groupId) {
    setLines(groupId, []);
  }

  return { installLayer, setLines, clear };
})();

window.Overlay3D = Overlay3D;
