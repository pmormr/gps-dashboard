/**
 * Generic elevated-data overlay for the MapLibre map.
 *
 * MapLibre (v5.24) has no elevated-line support, so anything that must float above
 * the terrain at a true altitude is drawn by three.js instead. This module is a
 * single MapLibre custom 3D layer hosting one three.js scene, composited into
 * MapLibre's own camera/GL context — the map stays MapLibre; this only adds a 3D
 * data plane on top of it. Reuses the vendored three.js that /globe ships, plus the
 * Line2 fat-line addons (screen-space line width; plain GL lines ignore linewidth).
 *
 * Geometry is keyed by *group* so the layer is not tied to any one dataset: drones
 * are the first consumer (`setLines('drone', …)`); van tracks, sensor columns, etc.
 * drop in later as additional groups. Each vertex is placed with
 * `MercatorCoordinate.fromLngLat([lon, lat], altMeters)` — and because altitudes
 * here are MSL and the terrain DEM is MSL, points float at the correct height above
 * the rendered terrain for free.
 *
 * Picking is done here too: three.js geometry isn't queryRenderedFeatures-able and
 * raycasting is unreliable against our synthetic (matrix-injected) camera, so
 * `pick(x, y)` projects each line's vertices through the last render matrix and runs
 * a nearest-segment test in screen space. A line may carry a `meta` payload that
 * `pick` returns to the caller (e.g. for a popup).
 */
import * as THREE from 'three';
import { Line2 } from '/static/vendor/three/lines/Line2.js';
import { LineGeometry } from '/static/vendor/three/lines/LineGeometry.js';
import { LineMaterial } from '/static/vendor/three/lines/LineMaterial.js';

const Overlay3D = (() => {
  const LAYER_ID = 'overlay-3d';
  const LINE_WIDTH = 3.5; // CSS px (LineMaterial screen-space width)
  const PICK_THRESHOLD = 8; // CSS px

  let map = null;
  let scene = null;
  let camera = null;
  let renderer = null;
  let lastMatrix = null; // mercator→clip, cached each render for picking
  let exaggeration = 1; // mirror of the map's terrain exaggeration (see setExaggeration)

  // groupId → { lines: input data, color?, opacity?, object: THREE.Group|null,
  //             pick: Array<{absMerc:[x,y,z][], coords:[lon,lat,alt][], meta}> }.
  // Data is retained across style swaps; `object`/`pick` are rebuilt on each onAdd.
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
   * Build a group's scene node + pick index from its line data. Each line sits at
   * its own first-vertex mercator origin with vertices stored *relative* to it, so
   * the tiny float32 deltas keep precision instead of riding on a ~0.25 absolute.
   */
  function buildGroup(g) {
    const root = new THREE.Group();
    const pick = [];
    for (const line of g.lines) {
      const pts = line.coords;
      if (!pts || pts.length < 2) continue;
      const absMerc = pts.map((p) => {
        const m = merc(p[0], p[1], p[2]);
        return [m.x, m.y, m.z];
      });
      const o = absMerc[0];
      const rel = new Array(pts.length * 3);
      for (let i = 0; i < pts.length; i++) {
        rel[i * 3] = absMerc[i][0] - o[0];
        rel[i * 3 + 1] = absMerc[i][1] - o[1];
        rel[i * 3 + 2] = absMerc[i][2] - o[2];
      }
      const geom = new LineGeometry();
      geom.setPositions(rel);
      const obj = new Line2(geom, new LineMaterial({
        color: line.color || g.color || '#ffffff',
        linewidth: line.width || g.width || LINE_WIDTH,
        transparent: true,
        opacity: line.opacity ?? g.opacity ?? 0.95,
      }));
      // Float at altitude × terrain exaggeration so tracks track the DEM as it's
      // stretched (sea level is the fixed point: z*=k). scale.z=k stretches the
      // relative geometry; position.z=originZ*k lifts the origin to match. originZ
      // keeps the unscaled origin for live re-scaling in setExaggeration.
      obj.userData.originZ = o[2];
      obj.position.set(o[0], o[1], o[2] * exaggeration);
      obj.scale.set(1, 1, exaggeration);
      // Mercator-space coords defeat three's frustum test; never cull.
      obj.frustumCulled = false;
      root.add(obj);
      pick.push({ absMerc, coords: pts, meta: line.meta });
    }
    return { root, pick };
  }

  /** Rebuild one group's scene node + pick index from its retained line data. */
  function refresh(groupId) {
    const g = groups.get(groupId);
    if (!g || !scene) return;
    if (g.object) {
      scene.remove(g.object);
      dispose(g.object);
      g.object = null;
    }
    g.pick = [];
    if (g.lines.length) {
      const built = buildGroup(g);
      g.object = built.root;
      g.pick = built.pick;
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
    render(gl, args) {
      if (!renderer) return;
      // v5 globe-era custom-layer API: the mercator→clip matrix is
      // args.defaultProjectionData.mainMatrix (older builds passed a bare array).
      const m = args?.defaultProjectionData?.mainMatrix ?? args;
      lastMatrix = m;
      camera.projectionMatrix.fromArray(m);
      // Fat lines need the drawing-buffer size each frame to size px width.
      const w = gl.drawingBufferWidth;
      const h = gl.drawingBufferHeight;
      scene.traverse((o) => {
        if (o.material && o.material.resolution) o.material.resolution.set(w, h);
      });
      renderer.resetState();
      renderer.render(scene, camera);
    },
    onRemove() {
      for (const g of groups.values()) {
        if (g.object) {
          dispose(g.object);
          g.object = null;
        }
        g.pick = [];
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
   * Replace a group's polylines. `lines` is
   * [{coords:[[lon,lat,altMeters],…], color?, meta?}]; `meta` is returned by pick().
   * `opts.color` / `opts.opacity` / `opts.width` are group-wide fallbacks.
   */
  function setLines(groupId, lines, opts = {}) {
    const g = groups.get(groupId) || { lines: [], object: null, pick: [] };
    g.lines = lines || [];
    if (opts.color != null) g.color = opts.color;
    if (opts.opacity != null) g.opacity = opts.opacity;
    if (opts.width != null) g.width = opts.width;
    groups.set(groupId, g);
    refresh(groupId);
  }

  /** Remove a group's polylines. */
  function clear(groupId) {
    setLines(groupId, []);
  }

  /**
   * Mirror the map's terrain exaggeration so floating lines stay registered with the
   * (stretched) DEM. Rescales existing lines in place — no geometry rebuild — and is
   * also picked up by buildGroup for lines added later.
   */
  function setExaggeration(k) {
    exaggeration = k;
    for (const g of groups.values()) {
      if (!g.object) continue;
      for (const obj of g.object.children) {
        obj.position.z = obj.userData.originZ * k;
        obj.scale.z = k;
      }
    }
    if (map) map.triggerRepaint();
  }

  /** [x,y,z,1]·mainMatrix (column-major) → clip-space {x,y,z,w}. */
  function project(m, x, y, z) {
    return {
      x: m[0] * x + m[4] * y + m[8] * z + m[12],
      y: m[1] * x + m[5] * y + m[9] * z + m[13],
      w: m[3] * x + m[7] * y + m[11] * z + m[15],
    };
  }

  /** Squared distance from point p to segment ab, all in 2D screen px. */
  function distSqToSeg(px, py, ax, ay, bx, by) {
    const dx = bx - ax;
    const dy = by - ay;
    const len2 = dx * dx + dy * dy;
    let t = len2 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0;
    t = Math.max(0, Math.min(1, t));
    const cx = ax + t * dx;
    const cy = ay + t * dy;
    return (px - cx) ** 2 + (py - cy) ** 2;
  }

  /**
   * Nearest line to a screen point (CSS px, matching map event `point`). Returns
   * `{ meta, lngLat }` for the closest line within PICK_THRESHOLD, else null.
   */
  function pick(px, py) {
    if (!lastMatrix || !map) return null;
    const canvas = map.getCanvas();
    const W = canvas.clientWidth;
    const H = canvas.clientHeight;
    const thresh2 = PICK_THRESHOLD ** 2;
    let best = null;
    let bestD = thresh2;
    for (const g of groups.values()) {
      for (const ln of g.pick || []) {
        let prev = null;
        for (let i = 0; i < ln.absMerc.length; i++) {
          const a = ln.absMerc[i];
          const c = project(lastMatrix, a[0], a[1], a[2] * exaggeration);
          const cur = c.w > 0 ? { x: (c.x / c.w * 0.5 + 0.5) * W, y: (1 - (c.y / c.w * 0.5 + 0.5)) * H, i } : null;
          if (prev && cur) {
            const d = distSqToSeg(px, py, prev.x, prev.y, cur.x, cur.y);
            if (d < bestD) {
              bestD = d;
              const v = ln.coords[i];
              best = { meta: ln.meta, lngLat: [v[0], v[1]] };
            }
          }
          prev = cur;
        }
      }
    }
    return best;
  }

  return { installLayer, setLines, clear, pick, setExaggeration };
})();

window.Overlay3D = Overlay3D;
