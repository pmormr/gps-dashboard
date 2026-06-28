/**
 * 3D constellation globe (island renderer).
 *
 * Renders the satellites we've logged (`/api/constellation`) in true 3D scale
 * around a textured Earth, in the same Earth-centred Earth-fixed (ECEF) frame the
 * server reconstructs positions in: +X through (lat 0, lon 0), +Y through
 * (lat 0, lon 90E), +Z through the north pole. Working directly in ECEF keeps the
 * observer marker and every satellite physically consistent — only the Earth
 * texture has to be aligned to that frame.
 *
 * The server sends each satellite's inertial-frame orbit fit and the client
 * propagates it here (one fit, one model), so the dot, trail, ring, and focused
 * orbit can't disagree. Per satellite: a dot at its current position — a set
 * satellite sits on the far side instead of frozen at the horizon — plus a faint
 * instantaneous orbit ring (the inertial plane rotated into ECEF at the window
 * end, so the dot sits on its ring). Clicking a satellite focuses it and draws
 * its full orbit as the propagated ECEF path — not a great-circle ring, because
 * over a period the true path precesses off any static plane; the dot and the
 * brighter recent trail are sub-segments of that same path. Trails are per-focus
 * by default (the Trails toggle shows them all). Without an orbit fit a dot falls
 * back to the last observed sample, and the all-trails view to the observed track
 * split at between-pass gaps so it never chords across the planet. A marker pins
 * the van's location, with sight-lines to the satellites above the horizon. PC
 * browsers only (WebGL). `?demo` synthesises a constellation for offline dev.
 *
 * Island form: `mountGlobe(root)` mounts into `root` and returns a teardown that
 * cancels the animation loop, drops listeners, and disposes the WebGL context —
 * the globe is lazy create/destroy (WebGL contexts are scarce on phones, so we
 * never keep more than one alive).
 */

import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { Line2 } from 'three/addons/lines/Line2.js'
import { LineGeometry } from 'three/addons/lines/LineGeometry.js'
import { LineMaterial } from 'three/addons/lines/LineMaterial.js'

/** Inertial-frame orbit fit the server sends per satellite (propagated client-side). */
interface OrbitFit {
  epoch: number
  radius_km: number
  n: number
  phase0: number
  u: [number, number, number]
  v: [number, number, number]
  normal: [number, number, number]
}

/** One reconstructed ECEF observation of a satellite. */
interface SatSample {
  t: string
  x: number
  y: number
  z: number
  snr?: number
  used?: boolean
}

/** A satellite from `/api/constellation`: its samples plus (optionally) a fit. */
interface SatRecord {
  gnssid: number
  svid: number
  samples: SatSample[]
  orbit?: OrbitFit | null
}

/** The observer anchor (latest fix) reconstructed to ECEF. */
interface Observer {
  lat: number
  lon: number
  alt?: number
  x: number
  y: number
  z: number
  timestamp?: string
}

/** The `/api/constellation` payload. */
interface ConstellationData {
  observer: Observer
  earth_radius_km: number
  window?: { end?: string }
  sats: SatRecord[]
}

/** A click-pick target, rebuilt each render. */
interface Pick {
  sat: SatRecord
  pos: THREE.Vector3
}

const DEG = Math.PI / 180

/** gpsd gnssid → display name + colour (matches the skyplot palette). */
const GNSS: Record<number, { name: string; color: number }> = {
  0: { name: 'GPS', color: 0x22c55e },
  1: { name: 'SBAS', color: 0x94a3b8 },
  2: { name: 'Galileo', color: 0xf59e0b },
  3: { name: 'BeiDou', color: 0xef4444 },
  5: { name: 'QZSS', color: 0xa78bfa },
  6: { name: 'GLONASS', color: 0x3b82f6 },
}

/**
 * Longitude alignment of the equirectangular Earth texture in the ECEF frame.
 * With `rotation.x = π/2`, the texture's prime meridian (u=0.5) already lands on
 * ECEF +X and the north pole on +Z, so no longitude shift is needed.
 */
const TEXTURE_OFFSET = 0.0

const GM_KM3_S2 = 398600.4418 // Earth's gravitational parameter, km^3/s^2
const PICK_PX = 18 // click pick tolerance, screen pixels

/** gnssid → RINEX constellation letter, for satellite names (G01, E11, …). */
const GNSS_LETTER: Record<number, string> = { 0: 'G', 1: 'S', 2: 'E', 3: 'C', 5: 'J', 6: 'R' }

const TRAIL_MIN_S = 1200 // shortest trail behind a just-seen satellite
const TRAIL_STEP_S = 120 // trail/path sample cadence (s)
const TRAIL_MAX_PTS = 128 // cap on trail samples

const FOCUS_RING_W = 4.0 // focused instantaneous great-circle ring width (CSS px); pulses
const FOCUS_RING_COLOR = 0xffffff // ring is white to stand apart from the coloured path/trail
const FOCUS_ORBIT_W = 5.0 // focused full-orbit fat-line width (CSS px); pulses
const FOCUS_TRAIL_W = 8.0 // focused recent-trail fat-line width (CSS px); pulses

/**
 * Mount the constellation globe into `root` and start rendering.
 *
 * @param root The island container (holds `#globe-container`, the panel, popup).
 * @returns A teardown that stops the loop, drops listeners, and frees the GL context.
 */
export function mountGlobe(root: HTMLElement): () => void {
  const demo = new URLSearchParams(location.search).has('demo')

  const $ = <T extends Element>(sel: string): T => root.querySelector(sel) as T
  const container = $<HTMLElement>('#globe-container')
  const statusEl = $<HTMLElement>('#status')
  const infoEl = $<HTMLElement>('#info')
  const legendEl = $<HTMLElement>('#legend')
  const popupEl = $<HTMLElement>('#sat-popup')

  /** Current container size (px); the renderer/camera/picking all read this. */
  const sizeOf = (): { w: number; h: number } => ({
    w: container.clientWidth || 1,
    h: container.clientHeight || 1,
  })
  let { w, h } = sizeOf()

  const renderer = new THREE.WebGLRenderer({ antialias: true })
  renderer.setPixelRatio(window.devicePixelRatio)
  renderer.setSize(w, h)
  container.appendChild(renderer.domElement)

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(0x05080f)

  const camera = new THREE.PerspectiveCamera(45, w / h, 10, 2_000_000)
  camera.up.set(0, 0, 1) // ECEF north is +Z
  camera.position.set(80000, -78000, 52000)

  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.minDistance = 8000
  controls.maxDistance = 600000
  controls.autoRotateSpeed = 0.175

  scene.add(new THREE.AmbientLight(0xffffff, 0.35))
  const sun = new THREE.DirectionalLight(0xffffff, 1.5)
  sun.position.set(1, 0.5, 0.55).multiplyScalar(1e6)
  scene.add(sun)

  // Starfield backdrop for depth/scale.
  scene.add(makeStars())

  let earth: THREE.Mesh | null = null
  let earthRadiusKm = 0
  const satGroup = new THREE.Group()
  const markerGroup = new THREE.Group()
  const highlightGroup = new THREE.Group()
  scene.add(satGroup)
  scene.add(markerGroup)
  scene.add(highlightGroup)

  /** Per-satellite pick targets, rebuilt each render for click picking. */
  const picks: Pick[] = []
  /** Observer ECEF (km), kept for the popup's sky-angle math. */
  let observerVec: THREE.Vector3 | null = null
  /** Window-end Unix seconds (the snapshot instant the orbits propagate to). */
  let windowEndUnix = 0

  /** UI state: window hours, layer toggles, and per-constellation visibility. */
  const state = {
    hours: 24,
    trails: false,
    rays: true,
    orbits: true,
    spin: true,
    hidden: new Set<number>(),
  }

  /**
   * Build the textured Earth at the given radius (km) and add it to the scene.
   *
   * The sphere's poles are rotated onto ECEF +Z, and the colour map's longitude is
   * shifted so painted continents line up with the ECEF frame (so the van marker,
   * placed by true ECEF, sits over the right place).
   */
  function buildEarth(radiusKm: number): void {
    if (earth) {
      scene.remove(earth)
      earth.geometry.dispose()
    }
    const loader = new THREE.TextureLoader()
    const map = loader.load('/static/img/earth_atmos_2048.jpg')
    map.colorSpace = THREE.SRGBColorSpace
    map.wrapS = THREE.RepeatWrapping
    map.offset.x = TEXTURE_OFFSET
    const material = new THREE.MeshPhongMaterial({
      map,
      specularMap: loader.load('/static/img/earth_specular_2048.jpg'),
      normalMap: loader.load('/static/img/earth_normal_2048.jpg'),
      shininess: 14,
      specular: 0x224466,
    })
    earth = new THREE.Mesh(new THREE.SphereGeometry(radiusKm, 96, 64), material)
    earth.rotation.x = Math.PI / 2 // align sphere poles to ECEF +Z
    scene.add(earth)
  }

  /** A sparse starfield far outside the constellation. */
  function makeStars(): THREE.Points {
    const n = 1500
    const pos = new Float32Array(n * 3)
    for (let i = 0; i < n; i++) {
      const v = new THREE.Vector3().randomDirection().multiplyScalar(900000)
      pos.set([v.x, v.y, v.z], i * 3)
    }
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3))
    return new THREE.Points(
      geo,
      new THREE.PointsMaterial({ color: 0x9fb3d0, size: 1200, sizeAttenuation: true }),
    )
  }

  /** Remove and dispose every object in a group. */
  function clearGroup(group: THREE.Group): void {
    for (const child of [...group.children]) {
      group.remove(child)
      const c = child as THREE.Mesh
      if (c.geometry) c.geometry.dispose()
      const mat = c.material as THREE.Material | THREE.Material[] | undefined
      if (Array.isArray(mat)) mat.forEach((m) => m.dispose())
      else if (mat) mat.dispose()
    }
  }

  /** A constellation-coloured arc line through ECEF points (km). */
  function arcLine(points: THREE.Vector3[], color: number, opacity = 0.7): THREE.Line {
    const geo = new THREE.BufferGeometry().setFromPoints(points)
    return new THREE.Line(geo, new THREE.LineBasicMaterial({ color, transparent: true, opacity }))
  }

  /** Renderer drawing-buffer size (px) — the resolution fat lines size their width against. */
  function drawingBufferSize(): THREE.Vector2 {
    return renderer.getDrawingBufferSize(new THREE.Vector2())
  }

  /**
   * A screen-space fat line through ECEF points (km). Plain GL lines ignore
   * `linewidth`, so the focus highlight uses Line2 (its width is in CSS px and
   * needs the renderer resolution, refreshed per frame in `updateHighlight`).
   */
  function fatLine(
    points: THREE.Vector3[],
    color: number,
    widthPx: number,
    opacity: number,
  ): Line2 {
    const flat: number[] = []
    for (const p of points) flat.push(p.x, p.y, p.z)
    const geo = new LineGeometry().setPositions(flat)
    const res = drawingBufferSize()
    const mat = new LineMaterial({ color, linewidth: widthPx, transparent: true, opacity })
    mat.resolution.set(res.x, res.y)
    const line = new Line2(geo, mat)
    line.frustumCulled = false
    return line
  }

  /** Unix seconds for a sample's canonical timestamp. */
  function sampleUnix(sample: SatSample): number {
    return Date.parse(sample.t) / 1000
  }

  /**
   * Split an observed track at between-pass gaps.
   *
   * Consecutive observations within a pass are ~60 s apart (a tiny angular step);
   * a multi-hour below-horizon gap shows up as a large jump between the set point
   * and the next rise. Breaking the polyline there stops the renderer drawing a
   * straight chord across the planet between two passes. Used only as the no-fit
   * fallback (a fitted satellite gets a smooth predicted trail instead).
   */
  function splitAtGaps(pts: THREE.Vector3[], maxStepDeg = 30): THREE.Vector3[][] {
    const minCos = Math.cos(maxStepDeg * DEG)
    const segs: THREE.Vector3[][] = []
    let seg: THREE.Vector3[] = []
    for (const p of pts) {
      if (seg.length) {
        const cos = seg[seg.length - 1].clone().normalize().dot(p.clone().normalize())
        if (cos < minCos) {
          segs.push(seg)
          seg = []
        }
      }
      seg.push(p)
    }
    if (seg.length) segs.push(seg)
    return segs
  }

  /** Whether a position is above the observer's local horizon. */
  function aboveHorizon(obsVec: THREE.Vector3, pos: THREE.Vector3): boolean {
    return pos.clone().sub(obsVec).dot(obsVec.clone().normalize()) > 0
  }

  /** Rebuild the satellite arcs, dots, and sight-lines from a constellation payload. */
  function render(data: ConstellationData): void {
    buildEarthIfNeeded(data.earth_radius_km)
    clearGroup(satGroup)
    clearGroup(markerGroup)
    picks.length = 0
    hidePopup()

    const obs = data.observer
    const obsVec = new THREE.Vector3(obs.x, obs.y, obs.z)
    observerVec = obsVec
    windowEndUnix =
      data.window && data.window.end ? Date.parse(data.window.end) / 1000 : Date.now() / 1000
    addMarker(obsVec)

    const seen = new Set<number>()
    let sampleCount = 0
    for (const sat of data.sats) {
      const meta = GNSS[sat.gnssid] || { name: 'Other', color: 0x64748b }
      seen.add(sat.gnssid)
      sampleCount += sat.samples.length

      const orbit = sat.orbit
      const lastSample = sat.samples[sat.samples.length - 1]
      // Live position propagated from the fitted orbit (a set SV is on the far
      // side); fall back to the last observed sample when the orbit can't be fit.
      const last = orbit
        ? orbitAt(orbit, windowEndUnix)
        : new THREE.Vector3(lastSample.x, lastSample.y, lastSample.z)

      picks.push({ sat, pos: last })

      const obj = new THREE.Group()
      obj.userData.gnssid = sat.gnssid
      obj.visible = !state.hidden.has(sat.gnssid)

      // Instantaneous orbit ring — its plane is rotated into ECEF at the window
      // end, so the dot sits on the ring.
      if (state.orbits && orbit) {
        obj.add(instantRing(orbit, windowEndUnix, meta.color, 0.22))
      }

      // The focused satellite always gets its trail (in showPopup); the Trails
      // toggle additionally draws every satellite's trail at once — informative but
      // busy (each is the true ground-relative path, which bends away from the ring
      // as Earth rotates), so it defaults off.
      if (state.trails) {
        if (orbit) {
          obj.add(arcLine(orbitTrail(orbit, windowEndUnix, sampleUnix(lastSample)), meta.color))
        } else {
          const pts = sat.samples.map((s) => new THREE.Vector3(s.x, s.y, s.z))
          for (const seg of splitAtGaps(pts)) {
            if (seg.length >= 2) obj.add(arcLine(seg, meta.color))
          }
        }
      }

      const snr = lastSample.snr || 0
      const dot = new THREE.Mesh(
        new THREE.SphereGeometry(180 + Math.min(50, snr) * 9, 12, 10),
        new THREE.MeshBasicMaterial({ color: meta.color }),
      )
      dot.position.copy(last)
      obj.add(dot)

      // Sight-line only when the satellite is above the horizon now; a set SV's dot
      // is behind the Earth, so no ray is drawn to it.
      if (state.rays && aboveHorizon(obsVec, last)) {
        const geo = new THREE.BufferGeometry().setFromPoints([obsVec, last])
        obj.add(
          new THREE.Line(
            geo,
            new THREE.LineBasicMaterial({ color: meta.color, transparent: true, opacity: 0.18 }),
          ),
        )
      }
      satGroup.add(obj)
    }

    renderLegend(seen)
    renderInfo(data, sampleCount)
    setStatus('')
  }

  /** Build the Earth once, at the radius the server reports. */
  function buildEarthIfNeeded(radiusKm: number): void {
    if (radiusKm && radiusKm !== earthRadiusKm) {
      earthRadiusKm = radiusKm
      buildEarth(radiusKm)
    }
  }

  // --- Orbit propagation (ported from common/orbits.py + common/satgeo.py) ---
  // The server sends each satellite's inertial-frame fit; the client propagates it
  // so the live dot, trail, instantaneous ring, and focused full orbit all derive
  // from one fit through one model and cannot disagree. θ(t) = phase0 + n·(t−epoch);
  // the position is on the ECI circle of radius r in plane (u, v), rotated into
  // ECEF by −GMST(t).

  /** Julian Date for a Unix timestamp. */
  function julianDate(unixS: number): number {
    return unixS / 86400 + 2440587.5
  }

  /** Greenwich Mean Sidereal Time (radians), IAU 1982 — matches common.satgeo. */
  function gmstRad(unixS: number): number {
    const t = (julianDate(unixS) - 2451545.0) / 36525.0
    const sec =
      67310.54841 +
      (876600.0 * 3600.0 + 8640184.812866) * t +
      0.093104 * t * t -
      6.2e-6 * t * t * t
    return (((sec % 86400) + 86400) % 86400) * ((2 * Math.PI) / 86400)
  }

  /** Rotate (x, y, z) about +Z by `ang` (right-handed); ECI→ECEF uses −GMST. */
  function rotateZ(x: number, y: number, z: number, ang: number): THREE.Vector3 {
    const c = Math.cos(ang)
    const s = Math.sin(ang)
    return new THREE.Vector3(x * c - y * s, x * s + y * c, z)
  }

  /** Orbital period (s) of a fitted orbit. */
  function orbitPeriod(o: OrbitFit): number {
    return (2 * Math.PI) / o.n
  }

  /** ECEF position (km) of a fitted orbit at a Unix time. */
  function orbitAt(o: OrbitFit, t: number): THREE.Vector3 {
    const th = o.phase0 + o.n * (t - o.epoch)
    const c = Math.cos(th)
    const s = Math.sin(th)
    const r = o.radius_km
    return rotateZ(
      r * (c * o.u[0] + s * o.v[0]),
      r * (c * o.u[1] + s * o.v[1]),
      r * (c * o.u[2] + s * o.v[2]),
      -gmstRad(t),
    )
  }

  /** Sample a fitted orbit's ECEF path over `[t0, t1]` into `nPts+1` points (km). */
  function orbitPath(o: OrbitFit, t0: number, t1: number, nPts: number): THREE.Vector3[] {
    const pts: THREE.Vector3[] = []
    for (let i = 0; i <= nPts; i++) pts.push(orbitAt(o, t0 + ((t1 - t0) * i) / nPts))
    return pts
  }

  /**
   * Recent predicted trail behind the dot: from where the SV was last seen up to
   * the window end, clamped to `[TRAIL_MIN_S, one period]` so a just-seen SV still
   * shows motion and a long-gone one traces at most one loop.
   */
  function orbitTrail(o: OrbitFit, endUnix: number, lastSeenUnix: number): THREE.Vector3[] {
    const span = Math.min(Math.max(endUnix - lastSeenUnix, TRAIL_MIN_S), orbitPeriod(o))
    const nPts = Math.min(TRAIL_MAX_PTS, Math.max(2, Math.ceil(span / TRAIL_STEP_S)))
    return orbitPath(o, endUnix - span, endUnix, nPts)
  }

  /** Right-handed in-plane basis (u, v) for a plane normal. */
  function planeBasis(normal: THREE.Vector3): { u: THREE.Vector3; v: THREE.Vector3 } {
    const n = normal.clone().normalize()
    const ref = Math.abs(n.z) < 0.9 ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(1, 0, 0)
    const u = new THREE.Vector3().crossVectors(ref, n).normalize()
    const v = new THREE.Vector3().crossVectors(n, u)
    return { u, v }
  }

  /** Points of a great-circle ring of the given radius in the plane normal to `normal`. */
  function greatCirclePoints(normal: THREE.Vector3, radiusKm: number): THREE.Vector3[] {
    const { u, v } = planeBasis(normal)
    const pts: THREE.Vector3[] = []
    const segments = 160
    for (let i = 0; i < segments; i++) {
      const t = (i / segments) * 2 * Math.PI
      pts.push(
        new THREE.Vector3()
          .addScaledVector(u, radiusKm * Math.cos(t))
          .addScaledVector(v, radiusKm * Math.sin(t)),
      )
    }
    return pts
  }

  /** A great-circle ring of the given radius in the plane with the given ECEF normal. */
  function greatCircle(
    normal: THREE.Vector3,
    radiusKm: number,
    color: number,
    opacity: number,
  ): THREE.LineLoop {
    const geo = new THREE.BufferGeometry().setFromPoints(greatCirclePoints(normal, radiusKm))
    return new THREE.LineLoop(geo, new THREE.LineBasicMaterial({ color, transparent: true, opacity }))
  }

  /**
   * The satellite's instantaneous orbit ring: its inertial plane rotated into ECEF
   * at time `t`, so the great circle passes through the dot at `t`. A static ring
   * only matches the orbit at one instant — over time the true ECEF path precesses
   * off it (that drift is what the focused full-period path shows).
   */
  function instantRing(orbit: OrbitFit, t: number, color: number, opacity: number): THREE.LineLoop {
    const nEcef = rotateZ(orbit.normal[0], orbit.normal[1], orbit.normal[2], -gmstRad(t))
    return greatCircle(nEcef, orbit.radius_km, color, opacity)
  }

  /** Closed point list of a satellite's instantaneous ring at time `t` (for fat lines). */
  function instantRingPoints(orbit: OrbitFit, t: number): THREE.Vector3[] {
    const nEcef = rotateZ(orbit.normal[0], orbit.normal[1], orbit.normal[2], -gmstRad(t))
    const pts = greatCirclePoints(nEcef, orbit.radius_km)
    pts.push(pts[0].clone()) // close the loop for the fat line
    return pts
  }

  /** Add the observer marker: a bright dot on the surface plus a short radial spike. */
  function addMarker(obsVec: THREE.Vector3): void {
    const dot = new THREE.Mesh(
      new THREE.SphereGeometry(160, 16, 12),
      new THREE.MeshBasicMaterial({ color: 0xffffff }),
    )
    dot.position.copy(obsVec)
    markerGroup.add(dot)

    const outward = obsVec.clone().normalize().multiplyScalar(obsVec.length() + 2400)
    const geo = new THREE.BufferGeometry().setFromPoints([obsVec, outward])
    markerGroup.add(
      new THREE.Line(
        geo,
        new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.7 }),
      ),
    )
  }

  /** Rebuild the constellation legend chips for the constellations present. */
  function renderLegend(seen: Set<number>): void {
    const ids = [...seen].sort((a, b) => a - b)
    legendEl.innerHTML = ids
      .map((id) => {
        const meta = GNSS[id] || { name: 'Other', color: 0x64748b }
        const hex = '#' + meta.color.toString(16).padStart(6, '0')
        const off = state.hidden.has(id) ? ' off' : ''
        return `<button class="leg${off}" data-gnss="${id}"><span class="sw" style="background:${hex}"></span>${meta.name}</button>`
      })
      .join('')
    legendEl.querySelectorAll<HTMLElement>('.leg').forEach((b) => {
      b.addEventListener('click', () => {
        const id = Number(b.dataset.gnss)
        if (state.hidden.has(id)) state.hidden.delete(id)
        else state.hidden.add(id)
        applyVisibility()
        renderLegend(seen)
      })
    })
  }

  /** Apply per-constellation visibility toggles to the satellite groups. */
  function applyVisibility(): void {
    for (const obj of satGroup.children) {
      obj.visible = !state.hidden.has(obj.userData.gnssid as number)
    }
  }

  /** Update the info readout (observer, counts, window, anchor time). */
  function renderInfo(data: ConstellationData, sampleCount: number): void {
    const o = data.observer
    const lat = o.lat.toFixed(4)
    const lon = o.lon.toFixed(4)
    const when = o.timestamp ? new Date(o.timestamp).toLocaleString() : '—'
    infoEl.innerHTML =
      `Observer <b>${lat}°, ${lon}°</b><br>` +
      `Satellites <b>${data.sats.length}</b> · samples <b>${sampleCount.toLocaleString()}</b><br>` +
      `Window <b>${state.hours}h</b> · anchored <b>${when}</b>`
  }

  /** Show or clear the centered status overlay. */
  function setStatus(msg: string): void {
    statusEl.textContent = msg
    statusEl.classList.toggle('hide', !msg)
  }

  /** Find the front-most satellite within the pick tolerance of a canvas point. */
  function pickAt(
    px: number,
    py: number,
    cw: number,
    ch: number,
  ): { sat: SatRecord; pos: THREE.Vector3; sx: number; sy: number } | null {
    let best: { sat: SatRecord; pos: THREE.Vector3; sx: number; sy: number } | null = null
    let bestCamDist = Infinity
    for (const p of picks) {
      if (state.hidden.has(p.sat.gnssid)) continue
      const ndc = p.pos.clone().project(camera)
      if (ndc.z > 1) continue // behind the camera
      const sx = (ndc.x * 0.5 + 0.5) * cw
      const sy = (-ndc.y * 0.5 + 0.5) * ch
      if (Math.hypot(sx - px, sy - py) > PICK_PX) continue
      const camDist = p.pos.distanceTo(camera.position)
      if (camDist < bestCamDist) {
        bestCamDist = camDist
        best = { sat: p.sat, pos: p.pos, sx, sy }
      }
    }
    return best
  }

  /** RINEX-style satellite name, e.g. G01 / E11. */
  function satName(gnssid: number, svid: number): string {
    return (GNSS_LETTER[gnssid] || '?') + String(svid).padStart(2, '0')
  }

  /** Circular-orbit speed (km/s) at a geocentric radius (km). */
  function orbitalSpeedKms(radiusKm: number): number {
    return Math.sqrt(GM_KM3_S2 / radiusKm)
  }

  /** Elevation/azimuth of a satellite as seen from the observer. */
  function skyAngles(pos: THREE.Vector3): { el: number; az: number } | null {
    if (!observerVec) return null
    const up = observerVec.clone().normalize()
    const los = pos.clone().sub(observerVec).normalize()
    const el = Math.asin(THREE.MathUtils.clamp(up.dot(los), -1, 1)) / DEG
    const east = new THREE.Vector3().crossVectors(new THREE.Vector3(0, 0, 1), up)
    if (east.lengthSq() < 1e-9) east.set(1, 0, 0)
    east.normalize()
    const north = new THREE.Vector3().crossVectors(up, east)
    let az = Math.atan2(los.dot(east), los.dot(north)) / DEG
    if (az < 0) az += 360
    return { el, az }
  }

  /**
   * Show the info popup for a satellite, and highlight its orbit + recent trail.
   *
   * Focusing a satellite reveals detail the cluttered overview hides: its
   * full-period orbit drawn as the propagated ECEF path (not a great-circle ring —
   * the dot and the brighter recent trail are sub-segments of this same curve, so
   * all three line up exactly, and its precession off the instantaneous ring is
   * the true Earth-rotation drift). The clean instantaneous ring, the full-period
   * path, and the thicker recent trail are all bold fat lines that pulse together
   * (`updateHighlight`). All in the highlight group, cleared on unfocus.
   */
  function showPopup(sat: SatRecord, pos: THREE.Vector3, sx: number, sy: number): void {
    clearGroup(highlightGroup)
    const meta = GNSS[sat.gnssid] || { name: 'Other', color: 0x64748b }
    const hex = '#' + meta.color.toString(16).padStart(6, '0')
    const radiusKm = pos.length()
    const altKm = radiusKm - earthRadiusKm
    const latest = sat.samples[sat.samples.length - 1]
    const sky = skyAngles(pos)
    const lastSeen = latest.t ? new Date(latest.t).toLocaleTimeString() : '—'
    const signal =
      typeof latest.snr === 'number' && latest.snr > 0
        ? `${latest.snr.toFixed(0)} dB-Hz${latest.used ? ' · used' : ''}`
        : '—'
    popupEl.innerHTML =
      '<div class="pop-head">' +
      `<div class="pop-name"><span class="sw" style="background:${hex}"></span>${satName(sat.gnssid, sat.svid)}</div>` +
      '<button class="pop-close" aria-label="Close">×</button>' +
      '</div>' +
      `<div class="pop-row"><span>System</span><b>${meta.name}</b></div>` +
      `<div class="pop-row"><span>Altitude</span><b>${Math.round(altKm).toLocaleString()} km</b></div>` +
      `<div class="pop-row"><span>Speed</span><b>${orbitalSpeedKms(radiusKm).toFixed(2)} km/s</b></div>` +
      (sky
        ? `<div class="pop-row"><span>Elevation</span><b>${sky.el.toFixed(0)}°</b></div>` +
          `<div class="pop-row"><span>Azimuth</span><b>${sky.az.toFixed(0)}°</b></div>`
        : '') +
      `<div class="pop-row"><span>Signal</span><b>${signal}</b></div>` +
      `<div class="pop-row"><span>Observed</span><b>${sat.samples.length} · ${lastSeen}</b></div>`
    popupEl.querySelector('.pop-close')?.addEventListener('click', hidePopup)
    popupEl.style.left = Math.min(container.clientWidth - 246, Math.max(8, sx + 14)) + 'px'
    popupEl.style.top = Math.min(container.clientHeight - 210, Math.max(8, sy + 14)) + 'px'
    popupEl.classList.remove('hidden')

    // Focused highlight: the clean instantaneous great-circle ring (white, so it
    // reads as the idealized orbit), the full-period propagated path, and the
    // thicker recent trail (both constellation-coloured) — all bold fat lines that
    // pulse together. The white ring is the tidy orbit circle; the coloured
    // path/trail are the true ECEF motion, which precesses off it as Earth rotates.
    if (sat.orbit) {
      const ringLine = fatLine(
        instantRingPoints(sat.orbit, windowEndUnix),
        FOCUS_RING_COLOR,
        FOCUS_RING_W,
        0.5,
      )
      ringLine.userData.pulse = { widthBase: FOCUS_RING_W, opacityBase: 0.5 }
      highlightGroup.add(ringLine)

      const period = orbitPeriod(sat.orbit)
      const orbitLine = fatLine(
        orbitPath(sat.orbit, windowEndUnix - period, windowEndUnix, 256),
        meta.color,
        FOCUS_ORBIT_W,
        0.55,
      )
      orbitLine.userData.pulse = { widthBase: FOCUS_ORBIT_W, opacityBase: 0.55 }
      highlightGroup.add(orbitLine)
      const trailLine = fatLine(
        orbitTrail(sat.orbit, windowEndUnix, sampleUnix(latest)),
        meta.color,
        FOCUS_TRAIL_W,
        0.7,
      )
      trailLine.userData.pulse = { widthBase: FOCUS_TRAIL_W, opacityBase: 0.7 }
      highlightGroup.add(trailLine)
    }

    const halo = new THREE.Mesh(
      new THREE.SphereGeometry(620, 16, 12),
      new THREE.MeshBasicMaterial({ color: 0xffffff, wireframe: true, transparent: true, opacity: 0.55 }),
    )
    halo.position.copy(pos)
    highlightGroup.add(halo)
  }

  /** Hide the popup and clear the selection halo. */
  function hidePopup(): void {
    popupEl.classList.add('hidden')
    clearGroup(highlightGroup)
  }

  /** Fetch the constellation for the current window (or demo data) and render. */
  async function load(): Promise<void> {
    setStatus('Loading constellation…')
    try {
      const data = demo ? demoData() : await fetchWindow(state.hours)
      if (!data.sats.length && !demo) {
        setStatus('No satellite observations in this window yet.')
        clearGroup(satGroup)
        clearGroup(markerGroup)
        return
      }
      render(data)
    } catch (e) {
      setStatus(typeof e === 'string' ? e : 'Could not load constellation data.')
    }
  }

  /** Fetch `/api/constellation` for a trailing window. */
  async function fetchWindow(hours: number): Promise<ConstellationData> {
    const end = new Date()
    const start = new Date(end.getTime() - hours * 3600 * 1000)
    const qs = `start=${start.toISOString()}&end=${end.toISOString()}`
    const resp = await fetch(`/api/constellation?${qs}`)
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}))
      throw body.error || 'No GPS fix available to anchor the observer.'
    }
    return resp.json()
  }

  /**
   * Synthesise a constellation for offline development (`?demo`): a Colorado
   * observer plus satellites on inclined circular orbits, emitted as the same
   * inertial orbit params the API returns so the client propagates them identically.
   */
  function demoData(): ConstellationData {
    const R_EARTH = 6371
    const obs = sphericalEcef(39.74, -104.99, R_EARTH)
    const now = Date.now() / 1000
    const defs: [number, number, number][] = [
      [0, 26560, 8],
      [6, 25510, 6],
      [2, 29600, 6],
      [3, 27910, 5],
    ]
    const sats: SatRecord[] = []
    let svid = 1
    for (const [gnssid, radius, count] of defs) {
      const n = Math.sqrt(GM_KM3_S2 / radius ** 3)
      for (let i = 0; i < count; i++) {
        const nrm = orbitNormal((50 + Math.random() * 12) * DEG, Math.random() * 2 * Math.PI)
        const { u, v } = planeBasis(nrm)
        const orbit: OrbitFit = {
          epoch: now,
          radius_km: radius,
          n,
          phase0: Math.random() * 2 * Math.PI,
          u: [u.x, u.y, u.z],
          v: [v.x, v.y, v.z],
          normal: [nrm.x, nrm.y, nrm.z],
        }
        const samples: SatSample[] = []
        for (let k = 0; k < 8; k++) {
          const t = now - 3600 + k * 450
          const p = orbitAt(orbit, t)
          samples.push({
            t: new Date(t * 1000).toISOString(),
            x: p.x,
            y: p.y,
            z: p.z,
            snr: 18 + Math.random() * 28,
            used: Math.random() > 0.4,
          })
        }
        sats.push({ gnssid, svid: svid++, samples, orbit })
      }
    }
    return {
      observer: {
        lat: 39.74,
        lon: -104.99,
        alt: 1600,
        x: obs.x,
        y: obs.y,
        z: obs.z,
        timestamp: new Date().toISOString(),
      },
      earth_radius_km: R_EARTH,
      window: { end: new Date(now * 1000).toISOString() },
      sats,
    }
  }

  /** Lat/lon on a sphere of radius r → ECEF vector (km). */
  function sphericalEcef(latDeg: number, lonDeg: number, r: number): THREE.Vector3 {
    const lat = latDeg * DEG
    const lon = lonDeg * DEG
    return new THREE.Vector3(
      r * Math.cos(lat) * Math.cos(lon),
      r * Math.cos(lat) * Math.sin(lon),
      r * Math.sin(lat),
    )
  }

  /** Orbit-plane normal for a demo orbit at the given inclination/RAAN. */
  function orbitNormal(inc: number, raan: number): THREE.Vector3 {
    return new THREE.Vector3(
      Math.sin(raan) * Math.sin(inc),
      -Math.cos(raan) * Math.sin(inc),
      Math.cos(inc),
    )
  }

  /** Wire the window-length, layer, and spin chips. */
  function bindControls(): void {
    root.querySelectorAll<HTMLElement>('#windows .chip').forEach((b) => {
      b.addEventListener('click', () => {
        root.querySelectorAll('#windows .chip').forEach((c) => c.classList.remove('active'))
        b.classList.add('active')
        state.hours = Number(b.dataset.h)
        load()
      })
    })
    bindToggle('t-orbits', 'orbits', () => load())
    bindToggle('t-trails', 'trails', () => load())
    bindToggle('t-rays', 'rays', () => load())
    bindToggle('t-spin', 'spin', () => {
      controls.autoRotate = state.spin
    })
    controls.autoRotate = state.spin
  }

  /** Wire a toggle chip to a boolean state flag. */
  function bindToggle(id: string, key: 'orbits' | 'trails' | 'rays' | 'spin', after: () => void): void {
    const btn = $<HTMLElement>('#' + id)
    btn.classList.toggle('active', state[key])
    btn.addEventListener('click', () => {
      state[key] = !state[key]
      btn.classList.toggle('active', state[key])
      after()
    })
  }

  /** Bind click-to-select (distinguished from a drag) and Escape-to-close. */
  function onKeydown(e: KeyboardEvent): void {
    if (e.key === 'Escape') hidePopup()
  }
  function onPointerDown(e: PointerEvent): void {
    downX = e.clientX
    downY = e.clientY
    downT = performance.now()
  }
  function onPointerUp(e: PointerEvent): void {
    if (Math.hypot(e.clientX - downX, e.clientY - downY) > 6 || performance.now() - downT > 500) {
      return // a drag-rotate, not a click
    }
    const rect = renderer.domElement.getBoundingClientRect()
    const hit = pickAt(e.clientX - rect.left, e.clientY - rect.top, rect.width, rect.height)
    if (hit) showPopup(hit.sat, hit.pos, hit.sx, hit.sy)
    else hidePopup()
  }
  let downX = 0
  let downY = 0
  let downT = 0

  /** Resize the renderer/camera to the container. */
  function onResize(): void {
    const s = sizeOf()
    w = s.w
    h = s.h
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    renderer.setSize(w, h)
  }

  /**
   * Per-frame upkeep of the focus highlight: keep fat-line widths sized to the
   * current drawing buffer, and pulse the focused orbit's width/opacity so the
   * selection breathes.
   */
  function updateHighlight(tSec: number): void {
    if (!highlightGroup.children.length) return
    const res = drawingBufferSize()
    const pulse = 0.5 + 0.5 * Math.sin(tSec * 2.4) // 0..1
    for (const o of highlightGroup.children) {
      const mat = (o as Line2).material as LineMaterial | undefined
      if (!mat || !mat.resolution) continue // the halo mesh has no fat-line material
      mat.resolution.set(res.x, res.y)
      const p = o.userData.pulse as { widthBase: number; opacityBase: number } | undefined
      if (p) {
        mat.linewidth = p.widthBase * (1 + 0.5 * pulse)
        mat.opacity = p.opacityBase + 0.35 * pulse
      }
    }
  }

  let rafId = 0
  function animate(): void {
    rafId = requestAnimationFrame(animate)
    controls.update()
    updateHighlight(performance.now() / 1000)
    renderer.render(scene, camera)
  }

  const resizeObserver = new ResizeObserver(onResize)
  resizeObserver.observe(container)
  window.addEventListener('keydown', onKeydown)
  renderer.domElement.addEventListener('pointerdown', onPointerDown)
  renderer.domElement.addEventListener('pointerup', onPointerUp)

  bindControls()
  buildEarth(6371)
  load()
  animate()

  /** Tear down: stop the loop, drop listeners, dispose GL resources. */
  return () => {
    cancelAnimationFrame(rafId)
    resizeObserver.disconnect()
    window.removeEventListener('keydown', onKeydown)
    controls.dispose()
    clearGroup(satGroup)
    clearGroup(markerGroup)
    clearGroup(highlightGroup)
    if (earth) {
      earth.geometry.dispose()
      ;(earth.material as THREE.Material).dispose()
    }
    scene.traverse((o) => {
      const m = o as THREE.Mesh
      if (m.geometry) m.geometry.dispose()
      const mat = m.material as THREE.Material | THREE.Material[] | undefined
      if (Array.isArray(mat)) mat.forEach((x) => x.dispose())
      else if (mat) mat.dispose()
    })
    renderer.dispose()
    renderer.forceContextLoss()
    renderer.domElement.remove()
  }
}
