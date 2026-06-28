/**
 * Live 3D satellite skyplot (island renderer).
 *
 * Polls the read-only `/api/gpsd/sky` endpoint (gpsd's SKY + TPV, no history
 * stored server-side) and renders the visible constellation on a tilted
 * wireframe hemisphere in plain canvas — no 3D library, so it stays
 * offline-clean.
 *
 * Each satellite is placed on a unit hemisphere by azimuth (compass angle) and
 * elevation (height), orthographically projected through a draggable
 * yaw/tilt camera. At tilt = 90° the projection collapses to the classic flat
 * top-down azimuth plot ("Top-down" button). Satellites are depth-sorted so
 * nearer ones occlude farther ones, with a vertical stem to the dome floor as
 * a height cue.
 *
 * Three overlays: a van glyph at dome center oriented to the GPS heading;
 * per-constellation visibility toggles (the legend chips); and client-side
 * per-satellite motion — trajectory trails and moving-average direction
 * vectors accumulated across polls.
 *
 * Island form: `mountSkyplot(root)` mounts into `root` and returns a teardown
 * that clears the poll/stat timers and drops the resize listener. Lazy
 * create/destroy — no point polling gpsd while the view is off-screen.
 */

const POLL_MS = 4000
const PASSES_HOURS = 3 // predicted-arc overlay window
const PASSES_REFRESH_MS = 120000 // predictions change slowly

/** Constellation → marker color. */
const GNSS: Record<string, string> = {
  GPS: '#22c55e',
  GLONASS: '#3b82f6',
  Galileo: '#f59e0b',
  BeiDou: '#ef4444',
  QZSS: '#a78bfa',
  SBAS: '#94a3b8',
  Other: '#64748b',
}
const GNSS_ORDER = ['GPS', 'GLONASS', 'Galileo', 'BeiDou', 'QZSS', 'SBAS', 'Other']

const DEG = Math.PI / 180
const TILT_DEFAULT = 55 * DEG
const TILT_MIN = 12 * DEG
const TILT_MAX = 90 * DEG
const MOVING_MS = 0.5 // speed (m/s) above which heading is trusted
const HISTORY_MAX = 45 // samples kept per satellite (~3 min at 4s)
const HISTORY_TTL_MS = 300000 // drop a satellite's track after 5 min unseen
const PREFS_KEY = 'skyplot.prefs'

/** One satellite from `/api/gpsd/sky`. */
interface Sat {
  prn: number
  az: number
  el: number
  ss?: number
  used?: boolean
  gnss: string
}

/** The `/api/gpsd/sky` payload (gpsd SKY + TPV). */
interface SkyMeta {
  connected?: boolean
  fix_mode?: number
  fix_label?: string
  track?: number
  speed?: number
  used?: number
  seen?: number
  hdop?: number
  vdop?: number
  pdop?: number
  xdop?: number
  ydop?: number
  gdop?: number
  tdop?: number
  satellites?: Sat[]
}

/** A predicted pass with an az/el track (from `/api/passes?track=1`). */
interface Pass {
  system: string
  name: string
  in_progress: boolean
  track?: [number, number][]
}

/** A per-satellite az/el history sample. */
interface HistPt {
  az: number
  el: number
  t: number
}

/** Persisted overlay/visibility prefs. */
interface Prefs {
  hidden: string[]
  trails: boolean
  vectors: boolean
  footprint: boolean
  geometry: boolean
  passes: boolean
}

/**
 * Mount the live skyplot into `root` and start polling.
 *
 * @param root The island container (holds the canvas, controls, legend, stats).
 * @returns A teardown that clears timers and drops the resize listener.
 */
export function mountSkyplot(root: HTMLElement): () => void {
  const search = new URLSearchParams(location.search)
  const demo = search.has('demo')

  const $ = <T extends Element>(sel: string): T => root.querySelector(sel) as T
  const canvas = $<HTMLCanvasElement>('#sky')
  const ctx = canvas.getContext('2d') as CanvasRenderingContext2D
  const wrap = $<HTMLElement>('#sky-wrap')
  const statusEl = $<HTMLElement>('#sky-status')
  const legendEl = $<HTMLElement>('#legend')

  /** Persisted prefs: which constellations are hidden + overlay toggles. */
  const prefs = loadPrefs()
  // Deep-link the predicted-arc overlay on with ?passes (handy for sharing/links).
  if (search.has('passes')) prefs.passes = true

  const view = {
    size: 0,
    yaw: 0,
    tilt: TILT_DEFAULT,
    sats: [] as Sat[],
    meta: null as SkyMeta | null,
    updatedAt: null as number | null,
    /** Constellations seen this session (stable toggle bar). */
    seen: new Set<string>(),
    /** gnss-prn → sample history for trails/vectors. */
    history: new Map<string, HistPt[]>(),
    /** Predicted upcoming passes (az/el arcs) from /api/passes. */
    passes: [] as Pass[],
  }

  /** Read stored prefs, falling back to defaults. */
  function loadPrefs(): Prefs {
    const base: Prefs = {
      hidden: [],
      trails: false,
      vectors: false,
      footprint: false,
      geometry: false,
      passes: false,
    }
    try {
      return Object.assign(base, JSON.parse(localStorage.getItem(PREFS_KEY) || '{}'))
    } catch {
      return base
    }
  }

  /** Persist the current prefs to localStorage. */
  function savePrefs(): void {
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(prefs))
    } catch {
      /* private mode — non-fatal */
    }
  }

  /** Stable history key for a satellite. */
  function satKey(s: Sat): string {
    return s.gnss + '-' + s.prn
  }

  /**
   * Rotate a world point (east, north, up) through the camera and project it to
   * screen space. At tilt = 90° the screen axes are (east, north) — top-down.
   *
   * @returns Screen offset from center (in dome radii) and a depth where larger
   *   is nearer the camera.
   */
  function cam(e: number, n: number, u: number): { x: number; y: number; depth: number } {
    const cy = Math.cos(view.yaw)
    const sy = Math.sin(view.yaw)
    const e1 = e * cy - n * sy
    const n1 = e * sy + n * cy
    const ct = Math.cos(view.tilt)
    const st = Math.sin(view.tilt)
    const vert = n1 * st + u * ct
    const depth = -n1 * ct + u * st
    return { x: e1, y: -vert, depth }
  }

  /** Project an azimuth/elevation onto the screen via the hemisphere + camera. */
  function project(az: number, el: number): { x: number; y: number; depth: number } {
    const azr = az * DEG
    const elr = el * DEG
    const ce = Math.cos(elr)
    return cam(ce * Math.sin(azr), ce * Math.cos(azr), Math.sin(elr))
  }

  /** Resize the canvas to its container (square) at device pixel ratio. */
  function resize(): void {
    const dpr = window.devicePixelRatio || 1
    const size = Math.min(wrap.clientWidth, 480)
    view.size = size
    canvas.style.height = size + 'px'
    canvas.width = Math.round(size * dpr)
    canvas.height = Math.round(size * dpr)
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    draw()
  }

  /** Convert a camera-space point to absolute canvas pixels. */
  function px(p: { x: number; y: number }, cx: number, cy: number, r: number): [number, number] {
    return [cx + p.x * r, cy + p.y * r]
  }

  /** Apply an alpha to a #rrggbb color. */
  function rgba(hex: string, a: number): string {
    const n = parseInt(hex.slice(1), 16)
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`
  }

  /** Color a DOP value by quality band (lower is better geometry). */
  function dopColor(d: number | undefined): string {
    if (typeof d !== 'number') return '#64748b'
    if (d < 2) return '#22c55e'
    if (d < 5) return '#f59e0b'
    if (d < 10) return '#f97316'
    return '#ef4444'
  }

  /** Word label for an overall (P)DOP value. */
  function dopLabel(d: number | undefined): string {
    if (typeof d !== 'number') return '—'
    if (d < 1) return 'Excellent'
    if (d < 2) return 'Good'
    if (d < 5) return 'Moderate'
    if (d < 10) return 'Fair'
    return 'Poor'
  }

  /** Draw the wireframe hemisphere: rings, meridians, zenith, compass labels. */
  function drawDome(cx: number, cy: number, r: number): void {
    ctx.lineWidth = 1
    for (const { el, strong } of [{ el: 0, strong: true }, { el: 30 }, { el: 60 }]) {
      ctx.beginPath()
      for (let az = 0; az <= 360; az += 4) {
        const [x, y] = px(project(az, el), cx, cy, r)
        az === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
      }
      ctx.closePath()
      ctx.strokeStyle = strong ? 'rgba(148,163,184,0.55)' : 'rgba(148,163,184,0.22)'
      ctx.stroke()
    }

    ctx.strokeStyle = 'rgba(148,163,184,0.18)'
    for (let az = 0; az < 360; az += 45) {
      ctx.beginPath()
      for (let el = 0; el <= 90; el += 3) {
        const [x, y] = px(project(az, el), cx, cy, r)
        el === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
      }
      ctx.stroke()
    }

    const [zx, zy] = px(project(0, 90), cx, cy, r)
    ctx.fillStyle = 'rgba(148,163,184,0.5)'
    ctx.beginPath()
    ctx.arc(zx, zy, 2, 0, 2 * Math.PI)
    ctx.fill()

    ctx.fillStyle = 'rgba(226,232,240,0.85)'
    ctx.font = '600 13px -apple-system, sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    for (const { az, label } of [
      { az: 0, label: 'N' },
      { az: 90, label: 'E' },
      { az: 180, label: 'S' },
      { az: 270, label: 'W' },
    ]) {
      const p = project(az, 0)
      const len = Math.hypot(p.x, p.y) || 1
      const out = len * r + 14
      ctx.fillText(label, cx + (p.x / len) * out, cy + (p.y / len) * out)
    }
  }

  /** Visible satellites after applying constellation toggles. */
  function visibleSats(): Sat[] {
    const hidden = new Set(prefs.hidden)
    return view.sats.filter((s) => !hidden.has(s.gnss))
  }

  /** Draw fading trajectory polylines from each satellite's sample history. */
  function drawTrails(cx: number, cy: number, r: number, sats: Sat[]): void {
    ctx.lineWidth = 1.6
    for (const s of sats) {
      const h = view.history.get(satKey(s))
      if (!h || h.length < 2) continue
      const color = GNSS[s.gnss] || GNSS.Other
      for (let i = 1; i < h.length; i++) {
        const [x0, y0] = px(project(h[i - 1].az, h[i - 1].el), cx, cy, r)
        const [x1, y1] = px(project(h[i].az, h[i].el), cx, cy, r)
        ctx.strokeStyle = rgba(color, 0.12 + 0.45 * (i / h.length))
        ctx.beginPath()
        ctx.moveTo(x0, y0)
        ctx.lineTo(x1, y1)
        ctx.stroke()
      }
    }
  }

  /**
   * Draw predicted pass arcs: each upcoming pass's az/el track as a dashed
   * polyline on the dome, coloured by constellation and honouring the legend
   * toggles. In-progress passes (the satellite is up now) show only the arc — its
   * live marker already labels it; passes that haven't risen yet get a hollow rise
   * marker and the satellite name, since those are the "coming up" cue.
   */
  function drawPasses(cx: number, cy: number, r: number): void {
    const hidden = new Set(prefs.hidden)
    ctx.save()
    ctx.lineWidth = 1.4
    ctx.font = '9px -apple-system, sans-serif'
    for (const p of view.passes) {
      if (hidden.has(p.system) || !p.track || p.track.length < 2) continue
      const color = GNSS[p.system] || GNSS.Other
      ctx.setLineDash([5, 4])
      ctx.strokeStyle = rgba(color, p.in_progress ? 0.6 : 0.38)
      ctx.beginPath()
      for (let i = 0; i < p.track.length; i++) {
        const [x, y] = px(project(p.track[i][0], p.track[i][1]), cx, cy, r)
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
      }
      ctx.stroke()
      if (!p.in_progress) {
        const [rx, ry] = px(project(p.track[0][0], p.track[0][1]), cx, cy, r)
        ctx.setLineDash([])
        ctx.lineWidth = 1.4
        ctx.strokeStyle = rgba(color, 0.85)
        ctx.beginPath()
        ctx.arc(rx, ry, 2.6, 0, 2 * Math.PI)
        ctx.stroke()
        ctx.fillStyle = rgba(color, 0.7)
        ctx.textAlign = 'left'
        ctx.textBaseline = 'middle'
        ctx.fillText(p.name, rx + 5, ry)
      }
    }
    ctx.restore()
  }

  /** Moving-average per-step velocity (deg az/el) from a satellite's history. */
  function avgVelocity(key: string): { daz: number; del: number } | null {
    const h = view.history.get(key)
    if (!h || h.length < 3) return null
    let daz = 0
    let del = 0
    let n = 0
    for (let i = 1; i < h.length; i++) {
      daz += ((h[i].az - h[i - 1].az + 540) % 360) - 180 // shortest az step
      del += h[i].el - h[i - 1].el
      n++
    }
    return n ? { daz: daz / n, del: del / n } : null
  }

  /** Draw a fixed-length direction arrow per satellite along its mean velocity. */
  function drawVectors(cx: number, cy: number, r: number, sats: Sat[]): void {
    const PXLEN = 22
    for (const s of sats) {
      const v = avgVelocity(satKey(s))
      if (!v) continue
      const p0 = project(s.az, s.el)
      const ahead = project(s.az + v.daz * 40, Math.max(0, Math.min(90, s.el + v.del * 40)))
      let dx = ahead.x - p0.x
      let dy = ahead.y - p0.y
      const len = Math.hypot(dx, dy)
      if (len < 1e-4) continue
      dx = (dx / len) * PXLEN
      dy = (dy / len) * PXLEN
      const [x0, y0] = px(p0, cx, cy, r)
      drawArrow(x0, y0, x0 + dx, y0 + dy, GNSS[s.gnss] || GNSS.Other)
    }
  }

  /** Draw a line with an arrowhead from (x0,y0) to (x1,y1). */
  function drawArrow(x0: number, y0: number, x1: number, y1: number, color: string): void {
    const ang = Math.atan2(y1 - y0, x1 - x0)
    ctx.strokeStyle = color
    ctx.fillStyle = color
    ctx.lineWidth = 1.6
    ctx.beginPath()
    ctx.moveTo(x0, y0)
    ctx.lineTo(x1, y1)
    ctx.stroke()
    const a = 5
    ctx.beginPath()
    ctx.moveTo(x1, y1)
    ctx.lineTo(x1 - a * Math.cos(ang - 0.5), y1 - a * Math.sin(ang - 0.5))
    ctx.lineTo(x1 - a * Math.cos(ang + 0.5), y1 - a * Math.sin(ang + 0.5))
    ctx.closePath()
    ctx.fill()
  }

  /**
   * Draw the DOP "precision footprint": a horizontal error ellipse on the dome
   * floor (semi-axes from xdop east / ydop north) plus a VDOP pillar rising from
   * the van, both colored by the PDOP quality band. The ellipse and pillar are
   * projected through the camera, so they tilt/foreshorten with the 3D view
   * (top-down collapses the pillar to a point — vertical extent is unseen from
   * straight above). Sizes are illustrative (grow with DOP), not metric error.
   */
  function drawFootprint(cx: number, cy: number, r: number): void {
    const m = view.meta
    if (!m) return
    const xd = typeof m.xdop === 'number' ? m.xdop : m.hdop
    const yd = typeof m.ydop === 'number' ? m.ydop : m.hdop
    if (typeof xd !== 'number' || typeof yd !== 'number') return
    const color = dopColor(m.pdop)
    const sE = (8 + xd * 18) / r // dome units → ellipse semi-axis east
    const sN = (8 + yd * 18) / r // → north

    ctx.beginPath()
    for (let a = 0; a <= 360; a += 6) {
      const ar = a * DEG
      const [x, y] = px(cam(sE * Math.cos(ar), sN * Math.sin(ar), 0), cx, cy, r)
      a === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
    }
    ctx.closePath()
    ctx.fillStyle = rgba(color, 0.16)
    ctx.fill()
    ctx.strokeStyle = rgba(color, 0.9)
    ctx.lineWidth = 1.5
    ctx.stroke()

    if (typeof m.vdop === 'number') {
      const h = (10 + m.vdop * 16) / r
      const [x0, y0] = px(cam(0, 0, 0), cx, cy, r)
      const [x1, y1] = px(cam(0, 0, h), cx, cy, r)
      ctx.strokeStyle = rgba(color, 0.85)
      ctx.lineWidth = 3
      ctx.beginPath()
      ctx.moveTo(x0, y0)
      ctx.lineTo(x1, y1)
      ctx.stroke()
      ctx.fillStyle = color
      ctx.beginPath()
      ctx.arc(x1, y1, 3.5, 0, 2 * Math.PI)
      ctx.fill()
    }
  }

  /** 2D convex hull (monotone chain) of screen points. */
  function convexHull(pts: { x: number; y: number }[]): { x: number; y: number }[] {
    if (pts.length < 3) return pts.slice()
    const p = pts.slice().sort((a, b) => a.x - b.x || a.y - b.y)
    const cross = (
      o: { x: number; y: number },
      a: { x: number; y: number },
      b: { x: number; y: number },
    ): number => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)
    const lower: { x: number; y: number }[] = []
    for (const q of p) {
      while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], q) <= 0)
        lower.pop()
      lower.push(q)
    }
    const upper: { x: number; y: number }[] = []
    for (let i = p.length - 1; i >= 0; i--) {
      const q = p[i]
      while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], q) <= 0)
        upper.pop()
      upper.push(q)
    }
    lower.pop()
    upper.pop()
    return lower.concat(upper)
  }

  /**
   * Shade the convex hull of the satellites used in the fix — the geometry that
   * produces the DOP. A large, well-spread hull means low DOP; a tight or
   * lopsided one means high DOP.
   */
  function drawHull(cx: number, cy: number, r: number, sats: Sat[]): void {
    const used = sats
      .filter((s) => s.used)
      .map((s) => {
        const [x, y] = px(project(s.az, s.el), cx, cy, r)
        return { x, y }
      })
    if (used.length < 3) return
    const hull = convexHull(used)
    ctx.beginPath()
    hull.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)))
    ctx.closePath()
    const color = dopColor(view.meta?.pdop)
    ctx.fillStyle = rgba(color, 0.1)
    ctx.fill()
    ctx.setLineDash([4, 3])
    ctx.strokeStyle = rgba(color, 0.55)
    ctx.lineWidth = 1.25
    ctx.stroke()
    ctx.setLineDash([])
  }

  /** Draw one satellite: stem to the dome floor, then the marker + PRN label. */
  function drawSat(s: Sat, cx: number, cy: number, r: number): void {
    const color = GNSS[s.gnss] || GNSS.Other
    const [tx, ty] = px(project(s.az, s.el), cx, cy, r)

    const azr = s.az * DEG
    const ce = Math.cos(s.el * DEG)
    const [fx, fy] = px(cam(ce * Math.sin(azr), ce * Math.cos(azr), 0), cx, cy, r)
    ctx.strokeStyle = 'rgba(148,163,184,0.28)'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(fx, fy)
    ctx.lineTo(tx, ty)
    ctx.stroke()

    const ss = typeof s.ss === 'number' ? s.ss : 0
    const radius = 4 + (Math.max(0, Math.min(50, ss)) / 50) * 5
    ctx.beginPath()
    ctx.arc(tx, ty, radius, 0, 2 * Math.PI)
    if (s.used) {
      ctx.fillStyle = color
      ctx.fill()
      ctx.lineWidth = 1.5
      ctx.strokeStyle = 'rgba(15,23,42,0.9)'
      ctx.stroke()
    } else {
      ctx.fillStyle = 'rgba(15,23,42,0.7)'
      ctx.fill()
      ctx.lineWidth = 1.75
      ctx.strokeStyle = color
      ctx.stroke()
    }

    if (s.prn != null) {
      ctx.fillStyle = 'rgba(226,232,240,0.7)'
      ctx.font = '9px -apple-system, sans-serif'
      ctx.textAlign = 'left'
      ctx.textBaseline = 'middle'
      ctx.fillText(String(s.prn), tx + radius + 2, ty)
    }
  }

  /** Rounded-rectangle path (manual — avoids relying on ctx.roundRect). */
  function roundRectPath(x: number, y: number, w: number, h: number, rad: number): void {
    ctx.beginPath()
    ctx.moveTo(x + rad, y)
    ctx.arcTo(x + w, y, x + w, y + h, rad)
    ctx.arcTo(x + w, y + h, x, y + h, rad)
    ctx.arcTo(x, y + h, x, y, rad)
    ctx.arcTo(x, y, x + w, y, rad)
    ctx.closePath()
  }

  /**
   * Draw the van at dome center, oriented to the GPS heading. Falls back to a
   * plain observer dot when stopped or heading is unavailable.
   */
  function drawVan(cx: number, cy: number): void {
    const m = view.meta
    const moving =
      m && typeof m.track === 'number' && typeof m.speed === 'number' && m.speed > MOVING_MS
    ctx.save()
    ctx.translate(cx, cy)
    if (moving) {
      const v = cam(Math.sin(m.track! * DEG), Math.cos(m.track! * DEG), 0)
      ctx.rotate(Math.atan2(v.y, v.x)) // glyph drawn pointing +x = forward
      // Body.
      roundRectPath(-11, -7, 22, 14, 3)
      ctx.fillStyle = '#e2e8f0'
      ctx.fill()
      ctx.lineWidth = 1.5
      ctx.strokeStyle = '#0f172a'
      ctx.stroke()
      // Windshield (front) + heading nose.
      ctx.fillStyle = '#38bdf8'
      ctx.fillRect(4, -5, 4, 10)
      ctx.beginPath()
      ctx.moveTo(11, -6)
      ctx.lineTo(20, 0)
      ctx.lineTo(11, 6)
      ctx.closePath()
      ctx.fill()
    } else {
      ctx.beginPath()
      ctx.arc(0, 0, 5, 0, 2 * Math.PI)
      ctx.fillStyle = '#e2e8f0'
      ctx.fill()
      ctx.lineWidth = 1.5
      ctx.strokeStyle = '#0f172a'
      ctx.stroke()
    }
    ctx.restore()
  }

  /** Repaint the whole plot from `view`. */
  function draw(): void {
    const size = view.size
    if (!size) return
    ctx.clearRect(0, 0, size, size)
    const cx = size / 2
    const cy = size / 2
    const r = size / 2 - 22

    drawDome(cx, cy, r)

    const sats = visibleSats()
    if (prefs.geometry) drawHull(cx, cy, r, sats)
    if (prefs.footprint) drawFootprint(cx, cy, r)
    if (prefs.passes) drawPasses(cx, cy, r)
    if (prefs.trails) drawTrails(cx, cy, r, sats)

    const ordered = sats
      .map((s) => ({ s, depth: project(s.az, s.el).depth }))
      .sort((a, b) => a.depth - b.depth)
    for (const { s } of ordered) drawSat(s, cx, cy, r)

    if (prefs.vectors) drawVectors(cx, cy, r, sats)
    drawVan(cx, cy)
  }

  /** 8-point compass label for a heading in degrees. */
  function cardinal(deg: number): string {
    return ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(deg / 45) % 8]
  }

  /** Push the live metadata into the stats strip. */
  function renderStats(): void {
    const m = view.meta
    const set = (id: string, val: string, cls?: string): void => {
      const el = $<HTMLElement>('#' + id)
      el.textContent = val
      el.className = 'v' + (cls ? ' ' + cls : '')
    }
    if (!m) return
    set('st-sats', `${m.used} / ${m.seen}`, (m.used ?? 0) >= 4 ? 'ok' : (m.used ?? 0) > 0 ? 'warn' : 'err')
    set('st-fix', m.fix_label || '—', (m.fix_mode ?? 0) >= 3 ? 'ok' : (m.fix_mode ?? 0) >= 2 ? 'warn' : 'err')
    set('st-head', typeof m.track === 'number' ? `${Math.round(m.track)}° ${cardinal(m.track)}` : '—')
    set('st-speed', typeof m.speed === 'number' ? `${(m.speed * 2.23694).toFixed(1)} mph` : '—')
    const q = $<HTMLElement>('#st-quality')
    q.textContent = dopLabel(m.pdop)
    q.className = 'v'
    q.style.color = dopColor(m.pdop)
    gauge('h', m.hdop)
    gauge('v', m.vdop)
    gauge('p', m.pdop)
    if (view.updatedAt) {
      const age = Math.round((Date.now() - view.updatedAt) / 1000)
      set('st-age', age <= 1 ? 'now' : `${age}s ago`)
    }
  }

  /** Position a DOP gauge marker (0–10 scale, clamped) and set its value text. */
  function gauge(k: string, val: number | undefined): void {
    const mark = $<HTMLElement>(`#dop-${k}-mark`)
    const vEl = $<HTMLElement>(`#dop-${k}-v`)
    if (typeof val !== 'number') {
      vEl.textContent = '—'
      mark.style.display = 'none'
      return
    }
    mark.style.display = 'block'
    mark.style.left = `${Math.max(0, Math.min(1, val / 10)) * 100}%`
    vEl.textContent = val.toFixed(2)
    vEl.style.color = dopColor(val)
  }

  /** Build the constellation toggle chips from the set seen this session. */
  function renderLegend(): void {
    const hidden = new Set(prefs.hidden)
    const chips = GNSS_ORDER.filter((g) => view.seen.has(g))
    legendEl.innerHTML =
      chips
        .map(
          (g) =>
            `<button class="leg-toggle${hidden.has(g) ? ' off' : ''}" data-gnss="${g}">` +
            `<span class="swatch" style="background:${GNSS[g]}"></span>${g}</button>`,
        )
        .join('') +
      '<span class="leg-note">tap to toggle · ● used in fix · ○ visible · size = signal</span>'
    legendEl.querySelectorAll<HTMLElement>('.leg-toggle').forEach((b) => {
      b.addEventListener('click', () => {
        const g = b.dataset.gnss!
        const set = new Set(prefs.hidden)
        set.has(g) ? set.delete(g) : set.add(g)
        prefs.hidden = [...set]
        savePrefs()
        renderLegend()
        draw()
      })
    })
  }

  /** Show/hide the centered status overlay (connecting / no fix / offline). */
  function setStatus(msg: string): void {
    if (msg) {
      statusEl.textContent = msg
      statusEl.classList.add('show')
    } else {
      statusEl.classList.remove('show')
    }
  }

  /** Append the latest az/el samples to per-satellite history and prune stale. */
  function updateHistory(sats: Sat[]): void {
    const now = Date.now()
    for (const s of sats) {
      const k = satKey(s)
      let h = view.history.get(k)
      if (!h) {
        h = []
        view.history.set(k, h)
      }
      h.push({ az: s.az, el: s.el, t: now })
      if (h.length > HISTORY_MAX) h.shift()
    }
    for (const [k, h] of view.history) {
      if (now - h[h.length - 1].t > HISTORY_TTL_MS) view.history.delete(k)
    }
  }

  /** Synthetic, slowly drifting sky for local checks (`?demo`) — no gpsd in dev. */
  let _demoSats:
    | { prn: number; gnss: string; az: number; el: number; ss: number; used: boolean; vaz: number; vel: number }[]
    | null = null
  function demoSky(): SkyMeta {
    if (!_demoSats) {
      const defs: [string, number][] = [
        ['GPS', 8],
        ['GLONASS', 6],
        ['Galileo', 6],
        ['BeiDou', 5],
        ['QZSS', 2],
        ['SBAS', 2],
      ]
      _demoSats = []
      let prn = 1
      for (const [gnss, n] of defs) {
        for (let i = 0; i < n; i++) {
          _demoSats.push({
            prn: prn++,
            gnss,
            az: Math.random() * 360,
            el: Math.random() * 80 + 5,
            ss: Math.random() * 38 + 12,
            used: Math.random() > 0.35,
            vaz: (Math.random() - 0.5) * 1.4, // deg/poll drift
            vel: (Math.random() - 0.5) * 0.5,
          })
        }
      }
    }
    for (const s of _demoSats) {
      s.az = (s.az + s.vaz + 360) % 360
      s.el += s.vel
      if (s.el > 88 || s.el < 4) s.vel *= -1
    }
    const sats: Sat[] = _demoSats.map(({ prn, gnss, az, el, ss, used }) => ({
      prn,
      gnss,
      az,
      el,
      ss,
      used,
    }))
    return {
      connected: true,
      fix_mode: 3,
      fix_label: '3D Fix',
      track: 295,
      speed: 12.4,
      used: sats.filter((s) => s.used).length,
      seen: sats.length,
      hdop: 0.8,
      vdop: 1.2,
      pdop: 1.5,
      xdop: 0.5,
      ydop: 0.7,
      gdop: 1.9,
      tdop: 0.9,
      satellites: sats,
    }
  }

  /** Fetch one sky snapshot, update state + history, and repaint. */
  async function poll(): Promise<void> {
    let data: SkyMeta
    try {
      data = demo ? demoSky() : await (await fetch('/api/gpsd/sky')).json()
    } catch {
      setStatus('gpsd unreachable')
      return
    }
    if (!data.connected) {
      setStatus('gpsd not connected')
      view.sats = []
      draw()
      return
    }
    view.meta = data
    view.sats = data.satellites || []
    view.updatedAt = Date.now()
    let added = false
    for (const s of view.sats) {
      if (!view.seen.has(s.gnss)) {
        view.seen.add(s.gnss)
        added = true
      }
    }
    updateHistory(view.sats)
    setStatus(view.sats.length ? '' : 'No satellites with a known position yet…')
    if (added) renderLegend()
    renderStats()
    draw()
  }

  /**
   * Fetch upcoming passes (next few hours, with full az/el tracks) for the arc
   * overlay and repaint. Independent of the live sky poll — predictions change
   * slowly, so this runs on its own slower timer.
   */
  async function loadPasses(): Promise<void> {
    try {
      const resp = await fetch(`/api/passes?hours=${PASSES_HOURS}&mask=0&track=1`)
      if (!resp.ok) {
        view.passes = []
        draw()
        return
      }
      const data = await resp.json()
      view.passes = (data.passes || []).filter((p: Pass) => Array.isArray(p.track))
      let added = false
      for (const p of view.passes) {
        if (!view.seen.has(p.system)) {
          view.seen.add(p.system)
          added = true
        }
      }
      if (added) renderLegend()
      draw()
    } catch {
      view.passes = []
    }
  }

  /** Wire the Predicted-passes toggle: fetch on enable, clear on disable. */
  function bindPasses(): void {
    const btn = $<HTMLElement>('#btn-passes')
    if (prefs.passes) btn.classList.add('active')
    btn.addEventListener('click', () => {
      prefs.passes = !prefs.passes
      btn.classList.toggle('active', prefs.passes)
      savePrefs()
      if (prefs.passes) loadPasses()
      else {
        view.passes = []
        draw()
      }
    })
    if (prefs.passes) loadPasses()
  }

  /** Pointer drag → yaw (horizontal) + tilt (vertical), clamped. */
  function bindDrag(): void {
    let dragging = false
    let lastX = 0
    let lastY = 0
    canvas.addEventListener('pointerdown', (e) => {
      dragging = true
      lastX = e.clientX
      lastY = e.clientY
      canvas.classList.add('dragging')
      canvas.setPointerCapture(e.pointerId)
    })
    canvas.addEventListener('pointermove', (e) => {
      if (!dragging) return
      view.yaw += (e.clientX - lastX) * 0.01
      view.tilt = Math.max(TILT_MIN, Math.min(TILT_MAX, view.tilt + (e.clientY - lastY) * 0.01))
      lastX = e.clientX
      lastY = e.clientY
      draw()
    })
    const end = (): void => {
      dragging = false
      canvas.classList.remove('dragging')
    }
    canvas.addEventListener('pointerup', end)
    canvas.addEventListener('pointercancel', end)
  }

  /** Wire an overlay toggle button to a boolean pref. */
  function bindToggle(id: string, key: 'trails' | 'vectors' | 'footprint' | 'geometry'): void {
    const btn = $<HTMLElement>('#' + id)
    if (prefs[key]) btn.classList.add('active')
    btn.addEventListener('click', () => {
      prefs[key] = !prefs[key]
      btn.classList.toggle('active', prefs[key])
      savePrefs()
      draw()
    })
  }

  $<HTMLElement>('#btn-topdown').addEventListener('click', () => {
    view.tilt = TILT_MAX
    view.yaw = 0
    draw()
  })
  $<HTMLElement>('#btn-reset').addEventListener('click', () => {
    view.tilt = TILT_DEFAULT
    view.yaw = 0
    draw()
  })
  bindToggle('btn-trails', 'trails')
  bindToggle('btn-vectors', 'vectors')
  bindToggle('btn-footprint', 'footprint')
  bindToggle('btn-geometry', 'geometry')
  bindPasses()

  const timers: number[] = []
  window.addEventListener('resize', resize)
  bindDrag()
  resize()
  poll()
  timers.push(window.setInterval(poll, POLL_MS))
  timers.push(window.setInterval(renderStats, 1000))
  timers.push(
    window.setInterval(() => {
      if (prefs.passes) loadPasses()
    }, PASSES_REFRESH_MS),
  )

  // The canvas/button listeners live on DOM inside `root`, which Svelte removes
  // on unmount; only the timers and the window resize listener need explicit
  // teardown to stop polling gpsd while the view is off-screen.
  return () => {
    timers.forEach((t) => clearInterval(t))
    window.removeEventListener('resize', resize)
  }
}
