/**
 * Generic elevated-data overlay for the MapLibre map.
 *
 * MapLibre has no elevated-line support, so anything that must float above the
 * terrain at a true altitude is drawn by three.js instead: a single MapLibre custom
 * 3D layer hosting one three.js scene, composited into MapLibre's own camera/GL
 * context. Geometry is keyed by *group* (drones are the first consumer) and each
 * vertex is placed with `MercatorCoordinate.fromLngLat([lon, lat], altMeters)` — and
 * because altitudes are MSL and the terrain DEM is MSL, points float at the correct
 * height above the rendered terrain for free.
 *
 * Picking projects each line's vertices through the last render matrix and runs a
 * nearest-segment test in screen space (raycasting is unreliable against the
 * matrix-injected camera). This module registers itself with map.ts via
 * `setOverlay3D` on import, so it can be lazily loaded only when a 3D layer is needed
 * (the drone toggle) and three stays out of the base map chunk.
 */

import { MercatorCoordinate } from 'maplibre-gl'
import type { CustomLayerInterface, Map as MlMap } from 'maplibre-gl'
import * as THREE from 'three'
import { Line2 } from 'three/addons/lines/Line2.js'
import { LineGeometry } from 'three/addons/lines/LineGeometry.js'
import { LineMaterial } from 'three/addons/lines/LineMaterial.js'

import { setOverlay3D, type Overlay3DApi, type Overlay3DLine } from './map'

const LAYER_ID = 'overlay-3d'
const LINE_WIDTH = 3.5 // CSS px (LineMaterial screen-space width)
const PICK_THRESHOLD = 8 // CSS px

interface PickEntry {
  absMerc: [number, number, number][]
  coords: [number, number, number][]
  meta: unknown
}

interface GroupState {
  lines: Overlay3DLine[]
  color?: string
  opacity?: number
  width?: number
  object: THREE.Group | null
  pick: PickEntry[]
}

/** v5 globe-era custom-layer render input: the matrix lives in defaultProjectionData. */
interface RenderArgs {
  defaultProjectionData?: { mainMatrix: number[] }
}

let map: MlMap | null = null
let scene: THREE.Scene | null = null
let camera: THREE.Camera | null = null
let renderer: THREE.WebGLRenderer | null = null
let lastMatrix: number[] | null = null // mercator→clip, cached each render for picking
let exaggeration = 1 // mirror of the map's terrain exaggeration

// groupId → retained line data + built scene node + pick index. Data survives style
// swaps; `object`/`pick` are rebuilt on each onAdd.
const groups = new Map<string, GroupState>()

/** [lon, lat, altMeters] → a MapLibre mercator-world coordinate (x,y in [0,1], z up). */
function merc(lon: number, lat: number, alt: number): MercatorCoordinate {
  return MercatorCoordinate.fromLngLat([lon, lat], alt || 0)
}

/** Free a THREE subtree's GPU resources. */
function dispose(obj: THREE.Object3D): void {
  obj.traverse((o) => {
    const mesh = o as Partial<THREE.Mesh>
    if (mesh.geometry) mesh.geometry.dispose()
    const mat = mesh.material
    if (mat && !Array.isArray(mat)) mat.dispose()
  })
}

/**
 * Build a group's scene node + pick index from its line data. Each line sits at its
 * own first-vertex mercator origin with vertices stored *relative* to it, so the tiny
 * float32 deltas keep precision instead of riding on a ~0.25 absolute.
 */
function buildGroup(g: GroupState): { root: THREE.Group; pick: PickEntry[] } {
  const root = new THREE.Group()
  const pick: PickEntry[] = []
  for (const line of g.lines) {
    const pts = line.coords
    if (!pts || pts.length < 2) continue
    const absMerc: [number, number, number][] = pts.map((p) => {
      const m = merc(p[0], p[1], p[2])
      return [m.x, m.y, m.z]
    })
    const o = absMerc[0]
    const rel = new Array<number>(pts.length * 3)
    for (let i = 0; i < pts.length; i++) {
      rel[i * 3] = absMerc[i][0] - o[0]
      rel[i * 3 + 1] = absMerc[i][1] - o[1]
      rel[i * 3 + 2] = absMerc[i][2] - o[2]
    }
    const geom = new LineGeometry()
    geom.setPositions(rel)
    const obj = new Line2(
      geom,
      new LineMaterial({
        color: new THREE.Color(line.color || g.color || '#ffffff').getHex(),
        linewidth: line.width || g.width || LINE_WIDTH,
        transparent: true,
        opacity: line.opacity ?? g.opacity ?? 0.95,
      }),
    )
    // Float at altitude × terrain exaggeration so tracks track the DEM as it's
    // stretched (sea level is the fixed point: z*=k). scale.z=k stretches the
    // relative geometry; position.z=originZ*k lifts the origin to match.
    obj.userData.originZ = o[2]
    obj.position.set(o[0], o[1], o[2] * exaggeration)
    obj.scale.set(1, 1, exaggeration)
    // Mercator-space coords defeat three's frustum test; never cull.
    obj.frustumCulled = false
    root.add(obj)
    pick.push({ absMerc, coords: pts, meta: line.meta })
  }
  return { root, pick }
}

/** Rebuild one group's scene node + pick index from its retained line data. */
function refresh(groupId: string): void {
  const g = groups.get(groupId)
  if (!g || !scene) return
  if (g.object) {
    scene.remove(g.object)
    dispose(g.object)
    g.object = null
  }
  g.pick = []
  if (g.lines.length) {
    const built = buildGroup(g)
    g.object = built.root
    g.pick = built.pick
    scene.add(g.object)
  }
  if (map) map.triggerRepaint()
}

// The MapLibre custom layer. setStyle drops it (calling onRemove); installLayer
// re-adds it and onAdd rebuilds the scene from the retained group data.
const layer: CustomLayerInterface = {
  id: LAYER_ID,
  type: 'custom',
  renderingMode: '3d',
  onAdd(m: MlMap, gl: WebGLRenderingContext | WebGL2RenderingContext) {
    map = m
    scene = new THREE.Scene()
    camera = new THREE.Camera()
    renderer = new THREE.WebGLRenderer({ canvas: m.getCanvas(), context: gl, antialias: true })
    renderer.autoClear = false
    for (const id of groups.keys()) refresh(id)
  },
  render(gl: WebGLRenderingContext | WebGL2RenderingContext, args: unknown) {
    if (!renderer || !scene || !camera) return
    // v5 globe-era custom-layer API: the mercator→clip matrix is
    // args.defaultProjectionData.mainMatrix (older builds passed a bare array).
    const a = args as RenderArgs | number[]
    const m = Array.isArray(a) ? a : (a.defaultProjectionData?.mainMatrix ?? [])
    lastMatrix = m
    camera.projectionMatrix.fromArray(m)
    // Fat lines need the drawing-buffer size each frame to size px width.
    const w = gl.drawingBufferWidth
    const h = gl.drawingBufferHeight
    scene.traverse((o) => {
      const mat = (o as Partial<THREE.Mesh>).material
      if (mat && !Array.isArray(mat) && mat instanceof LineMaterial) mat.resolution.set(w, h)
    })
    renderer.resetState()
    renderer.render(scene, camera)
  },
  onRemove() {
    for (const g of groups.values()) {
      if (g.object) {
        dispose(g.object)
        g.object = null
      }
      g.pick = []
    }
    renderer = null
    scene = null
    camera = null
  },
}

/** Add the custom layer if absent. Safe to call on every styledata. */
function installLayer(m: MlMap): void {
  if (!m.getLayer(LAYER_ID)) m.addLayer(layer)
}

/**
 * Replace a group's polylines. `opts.color`/`opacity`/`width` are group-wide
 * fallbacks; `meta` on a line is returned by pick().
 */
function setLines(
  groupId: string,
  lines: Overlay3DLine[],
  opts: { color?: string; opacity?: number; width?: number } = {},
): void {
  const g: GroupState = groups.get(groupId) ?? { lines: [], object: null, pick: [] }
  g.lines = lines || []
  if (opts.color != null) g.color = opts.color
  if (opts.opacity != null) g.opacity = opts.opacity
  if (opts.width != null) g.width = opts.width
  groups.set(groupId, g)
  refresh(groupId)
}

/** Remove a group's polylines. */
function clear(groupId: string): void {
  setLines(groupId, [])
}

/**
 * Mirror the map's terrain exaggeration so floating lines stay registered with the
 * (stretched) DEM. Rescales existing lines in place — no geometry rebuild.
 */
function setExaggeration(k: number): void {
  exaggeration = k
  for (const g of groups.values()) {
    if (!g.object) continue
    for (const obj of g.object.children) {
      obj.position.z = (obj.userData.originZ as number) * k
      obj.scale.z = k
    }
  }
  if (map) map.triggerRepaint()
}

/** [x,y,z,1]·mainMatrix (column-major) → clip-space {x,y,w}. */
function project(m: number[], x: number, y: number, z: number): { x: number; y: number; w: number } {
  return {
    x: m[0] * x + m[4] * y + m[8] * z + m[12],
    y: m[1] * x + m[5] * y + m[9] * z + m[13],
    w: m[3] * x + m[7] * y + m[11] * z + m[15],
  }
}

/** Squared distance from point p to segment ab, all in 2D screen px. */
function distSqToSeg(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax
  const dy = by - ay
  const len2 = dx * dx + dy * dy
  let t = len2 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0
  t = Math.max(0, Math.min(1, t))
  const cx = ax + t * dx
  const cy = ay + t * dy
  return (px - cx) ** 2 + (py - cy) ** 2
}

/**
 * Nearest line to a screen point (CSS px). Returns `{ meta, lngLat }` for the closest
 * line within PICK_THRESHOLD, else null.
 */
function pick(px: number, py: number): { meta: unknown; lngLat: [number, number] } | null {
  if (!lastMatrix || !map) return null
  const canvas = map.getCanvas()
  const W = canvas.clientWidth
  const H = canvas.clientHeight
  const thresh2 = PICK_THRESHOLD ** 2
  let best: { meta: unknown; lngLat: [number, number] } | null = null
  let bestD = thresh2
  for (const g of groups.values()) {
    for (const ln of g.pick || []) {
      let prev: { x: number; y: number } | null = null
      for (let i = 0; i < ln.absMerc.length; i++) {
        const a = ln.absMerc[i]
        const c = project(lastMatrix, a[0], a[1], a[2] * exaggeration)
        const cur =
          c.w > 0
            ? { x: ((c.x / c.w) * 0.5 + 0.5) * W, y: (1 - ((c.y / c.w) * 0.5 + 0.5)) * H }
            : null
        if (prev && cur) {
          const d = distSqToSeg(px, py, prev.x, prev.y, cur.x, cur.y)
          if (d < bestD) {
            bestD = d
            const v = ln.coords[i]
            best = { meta: ln.meta, lngLat: [v[0], v[1]] }
          }
        }
        prev = cur
      }
    }
  }
  return best
}

const Overlay3D: Overlay3DApi = { installLayer, setLines, clear, pick, setExaggeration }

// Register with map.ts on import (this module is lazily imported when 3D data is needed).
setOverlay3D(Overlay3D)
