/**
 * TimeStrip — the Selection window's timeline lane, rendered entirely in one
 * canvas. It draws the window's layers in one pass — point-density coverage,
 * stop dwell blocks, annotation range bands + point ticks — and turns
 * direct manipulation into Selection-axis actions: drag a
 * rubber-band to zoom, wheel to zoom around the cursor (preview per step,
 * commit debounced so each notch doesn't refetch), double-click/Backspace to
 * step back out, ←/→ to shift, +/− to zoom. There is no local sub-selection —
 * the window is the only time object; every gesture commits through the
 * actions the mounting view wires to the Selection store.
 *
 * Island form: `mountTimeStrip(canvas, tooltip, actions)` scopes to the passed
 * elements (no getElementById) and returns the control surface + a `destroy`.
 * Kept imperative — it's a custom canvas widget, not reactive UI. All times are
 * ms since epoch.
 */

import type { TrackPoint } from './geo'
import { MAX_WINDOW_MS } from './stores/selection.svelte'

/** An annotation band/tick the strip overlays (the relevant subset). */
export interface StripAnnotation {
  start_time: string
  end_time?: string | null
  name?: string
}

/** Selection-axis actions the strip's gestures commit through. */
export interface TimeStripActions {
  /** Rubber-band release / wheel commit / `+` — jump to an explicit window. */
  onZoom(loMs: number, hiMs: number): void
  /** `−` — widen around center (the store caps the width). */
  onWiden(): void
  /** ←/→ — move by one window-width. */
  onShift(dir: -1 | 1): void
  /** Double-click / Backspace — step back out of the last zoom. */
  onBack(): void
  /** Single-click on an annotation band/tick — jump to that window. Optional. */
  onAnnotationClick?(a: StripAnnotation): void
  /** Hover moved to this time (null = left the strip / started a drag). Optional. */
  onHover?(ms: number | null): void
}

export interface TimeStripHandle {
  setData(d: { startMs: number; endMs: number; points: TrackPoint[]; annotations?: StripAnnotation[] }): void
  setAnnotations(anns: StripAnnotation[]): void
  destroy(): void
}

const GAP_CAP_MS = 15 * 60 * 1000 // density coverage gap cap
const PAD_TOP = 5 // top lane: annotation bands + ticks
const PAD_BOT = 4 // bottom lane: stop dwell blocks
const MIN_SPAN_MS = 60 * 1000 // wheel/keyboard zoom-in floor
const DRAG_PX = 4 // pointer travel below this is a click, not a rubber-band
const MIN_ZOOM_MS = 1000 // ignore degenerate rubber-bands
const WHEEL_IDLE_MS = 200 // wheel commit debounce (preview redraws per step)
const CLICK_DELAY_MS = 250 // single-click delay so a double-click (back) can cancel it

/**
 * Scale a domain around an anchor time by `factor` (>1 widens), holding the
 * anchor's position fixed and clamping the span. Pure — the wheel-zoom math.
 */
export function zoomDomain(
  d: { startMs: number; endMs: number },
  anchorMs: number,
  factor: number,
  minSpanMs: number = MIN_SPAN_MS,
  maxSpanMs: number = MAX_WINDOW_MS,
): { startMs: number; endMs: number } {
  const span = d.endMs - d.startMs
  const newSpan = Math.min(maxSpanMs, Math.max(minSpanMs, span * factor))
  const frac = span > 0 ? (anchorMs - d.startMs) / span : 0.5
  const startMs = anchorMs - frac * newSpan
  return { startMs, endMs: startMs + newSpan }
}

/** Mount the strip onto a canvas (+ optional tooltip el) and return its controls. */
export function mountTimeStrip(
  canvas: HTMLCanvasElement,
  tooltip: HTMLElement | null,
  actions: TimeStripActions,
): TimeStripHandle {
  const ctx = canvas.getContext('2d') as CanvasRenderingContext2D
  let domain: { startMs: number; endMs: number } | null = null
  // Wheel preview — drawn immediately per step, committed (one refetch) on idle.
  let pending: { startMs: number; endMs: number } | null = null
  let points: TrackPoint[] = []
  let annotations: StripAnnotation[] = []
  let drag: { startX: number; curX: number; moved: boolean } | null = null
  let wheelTimer: number | undefined
  let clickTimer: number | undefined

  canvas.tabIndex = 0
  canvas.setAttribute(
    'aria-label',
    'Timeline — drag to zoom, scroll to zoom, double-click to go back',
  )

  // ── geometry (against the preview domain while one is pending) ──
  const view = (): { startMs: number; endMs: number } | null => pending ?? domain
  const cssW = (): number => canvas.clientWidth
  const cssH = (): number => canvas.clientHeight
  function xOfMs(ms: number): number {
    const v = view()
    if (!v) return 0
    const s = v.endMs - v.startMs
    return s > 0 ? ((ms - v.startMs) / s) * cssW() : 0
  }
  function msOfX(px: number): number {
    const v = view()
    if (!v) return 0
    return v.startMs + (px / Math.max(1, cssW())) * (v.endMs - v.startMs)
  }
  function clampMs(ms: number): number {
    const v = view()
    return v ? Math.max(v.startMs, Math.min(v.endMs, ms)) : ms
  }

  function fmtDur(mins: number): string {
    if (mins < 60) return `${mins}m`
    const h = Math.floor(mins / 60)
    if (h < 24) {
      const m = mins % 60
      return m ? `${h}h ${m}m` : `${h}h`
    }
    const d = Math.floor(h / 24)
    const rh = h % 24
    return rh ? `${d}d ${rh}h` : `${d}d`
  }

  // ── drawing ──
  function draw(): void {
    const w = cssW()
    const h = cssH()
    const dpr = window.devicePixelRatio || 1
    canvas.width = Math.round(w * dpr)
    canvas.height = Math.round(h * dpr)
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, w, h)
    const v = view()
    if (!v || v.endMs <= v.startMs || w < 1) return
    drawDensity(w, h, v)
    drawStops(h, v)
    drawAnnotations(v)
    drawRubberBand(h)
  }

  function drawDensity(w: number, h: number, v: { startMs: number; endMs: number }): void {
    const cols = Math.max(1, Math.floor(w))
    const counts = new Float64Array(cols)
    const covered = new Uint8Array(cols)
    const colOf = (ms: number): number => Math.min(cols - 1, Math.max(0, Math.round(xOfMs(ms))))
    const fillCols = (loMs: number, hiMs: number): void => {
      const a = colOf(Math.max(loMs, v.startMs))
      const b = colOf(Math.min(hiMs, v.endMs))
      for (let c = a; c <= b; c++) covered[c] = 1
    }
    let prev: number | null = null
    for (const p of points) {
      if (p.kind === 'stop' && p.dwell_start && p.dwell_end) {
        fillCols(new Date(p.dwell_start).getTime(), new Date(p.dwell_end).getTime())
        prev = null
        continue
      }
      const ms = new Date(p.timestamp).getTime()
      if (ms < v.startMs || ms > v.endMs) {
        prev = null
        continue
      }
      const c = colOf(ms)
      counts[c] += 1
      covered[c] = 1
      if (prev != null && ms - prev <= GAP_CAP_MS) fillCols(prev, ms)
      prev = ms
    }
    let maxC = 0
    for (let c = 0; c < cols; c++) if (counts[c] > maxC) maxC = counts[c]
    const bodyBot = h - PAD_BOT
    const bodyH = bodyBot - PAD_TOP
    const floor = 0.4
    for (let c = 0; c < cols; c++) {
      if (!covered[c]) continue
      const dens = maxC > 0 ? Math.sqrt(counts[c] / maxC) : 0
      const barH = Math.max(2, (floor + (1 - floor) * dens) * bodyH)
      ctx.fillStyle = `rgba(148, 163, 184, ${0.5 + 0.45 * dens})`
      ctx.fillRect(c, bodyBot - barH, 1, barH)
    }
  }

  function drawStops(h: number, v: { startMs: number; endMs: number }): void {
    ctx.fillStyle = 'rgba(239, 68, 68, 0.85)'
    for (const p of points) {
      if (p.kind !== 'stop' || !p.dwell_start || !p.dwell_end) continue
      const sMs = new Date(p.dwell_start).getTime()
      const eMs = new Date(p.dwell_end).getTime()
      if (eMs < v.startMs || sMs > v.endMs) continue
      const x0 = xOfMs(Math.max(sMs, v.startMs))
      const x1 = xOfMs(Math.min(eMs, v.endMs))
      ctx.fillRect(x0, h - PAD_BOT, Math.max(1.5, x1 - x0), PAD_BOT)
    }
  }

  function drawAnnotations(v: { startMs: number; endMs: number }): void {
    for (const a of annotations) {
      const sMs = new Date(a.start_time).getTime()
      if (a.end_time) {
        const eMs = new Date(a.end_time).getTime()
        if (eMs < v.startMs || sMs > v.endMs) continue
        const x0 = xOfMs(Math.max(sMs, v.startMs))
        const x1 = xOfMs(Math.min(eMs, v.endMs))
        ctx.fillStyle = 'rgba(34, 211, 238, 0.65)'
        ctx.fillRect(x0, 0, Math.max(1.5, x1 - x0), 3)
      } else {
        if (sMs < v.startMs || sMs > v.endMs) continue
        ctx.fillStyle = '#f59e0b'
        ctx.fillRect(xOfMs(sMs) - 1, 0, 2, PAD_TOP + 4)
      }
    }
  }

  function drawRubberBand(h: number): void {
    if (!drag || !drag.moved) return
    const x0 = Math.min(drag.startX, drag.curX)
    const x1 = Math.max(drag.startX, drag.curX)
    ctx.fillStyle = 'rgba(59, 130, 246, 0.25)'
    ctx.fillRect(x0, 0, x1 - x0, h)
    ctx.fillStyle = 'rgba(59, 130, 246, 0.9)'
    ctx.fillRect(x0, 0, 1.5, h)
    ctx.fillRect(x1 - 1.5, 0, 1.5, h)
  }

  // ── interaction ──
  function localX(e: { clientX: number }): number {
    return e.clientX - canvas.getBoundingClientRect().left
  }

  function onPointerDown(e: PointerEvent): void {
    if (!view()) return
    const px = localX(e)
    drag = { startX: px, curX: px, moved: false }
    try {
      canvas.setPointerCapture(e.pointerId)
    } catch {
      /* not capturable — non-fatal */
    }
    hideTooltip()
    actions.onHover?.(null)
    e.preventDefault()
  }

  function onPointerMove(e: PointerEvent): void {
    const px = localX(e)
    if (!drag) {
      updateHover(px)
      return
    }
    drag.curX = px
    if (Math.abs(px - drag.startX) >= DRAG_PX) drag.moved = true
    draw()
    e.preventDefault()
  }

  function onPointerUp(e: PointerEvent): void {
    if (!drag) return
    const d = drag
    drag = null
    try {
      canvas.releasePointerCapture(e.pointerId)
    } catch {
      /* non-fatal */
    }
    draw()
    if (!d.moved) {
      // A click: jump to the annotation under the cursor, delayed so a
      // double-click (back) cancels it.
      const a = actions.onAnnotationClick ? annotationAt(msOfX(d.startX), d.startX) : null
      if (a) {
        if (clickTimer) clearTimeout(clickTimer)
        clickTimer = window.setTimeout(() => {
          clickTimer = undefined
          actions.onAnnotationClick!(a)
        }, CLICK_DELAY_MS)
      }
      return
    }
    const lo = clampMs(msOfX(Math.min(d.startX, d.curX)))
    const hi = clampMs(msOfX(Math.max(d.startX, d.curX)))
    if (hi - lo >= MIN_ZOOM_MS) actions.onZoom(lo, hi)
  }

  /**
   * The annotation under a strip position: point ticks win (±5px), then the
   * narrowest range band covering the time (most specific on overlap).
   */
  function annotationAt(ms: number, px: number): StripAnnotation | null {
    let hit: StripAnnotation | null = null
    let hitSpan = Infinity
    for (const a of annotations) {
      const sMs = new Date(a.start_time).getTime()
      if (!a.end_time) {
        if (Math.abs(xOfMs(sMs) - px) <= 5) return a
        continue
      }
      const eMs = new Date(a.end_time).getTime()
      if (ms >= sMs && ms <= eMs && eMs - sMs < hitSpan) {
        hit = a
        hitSpan = eMs - sMs
      }
    }
    return hit
  }

  function onWheel(e: WheelEvent): void {
    const v = view()
    if (!v) return
    e.preventDefault()
    // ~1.16× per mouse notch (deltaY ≈ 100); smooth for pixel-delta trackpads.
    pending = zoomDomain(v, msOfX(localX(e)), Math.pow(1.0015, e.deltaY))
    draw()
    if (wheelTimer) clearTimeout(wheelTimer)
    wheelTimer = window.setTimeout(commitWheel, WHEEL_IDLE_MS)
  }

  function commitWheel(): void {
    wheelTimer = undefined
    if (!pending) return
    const p = pending
    // Adopt the preview as the domain now; the commit's refetch lands via setData.
    domain = p
    pending = null
    actions.onZoom(p.startMs, p.endMs)
  }

  function onDblClick(e: MouseEvent): void {
    e.preventDefault()
    if (clickTimer) {
      clearTimeout(clickTimer)
      clickTimer = undefined
    }
    actions.onBack()
  }

  function onKeyDown(e: KeyboardEvent): void {
    const v = view()
    if (!v) return
    let handled = true
    if (e.key === 'ArrowLeft') {
      actions.onShift(-1)
    } else if (e.key === 'ArrowRight') {
      actions.onShift(1)
    } else if (e.key === '+' || e.key === '=') {
      const span = v.endMs - v.startMs
      const half = Math.max(span / 2, MIN_SPAN_MS) / 2
      const c = (v.startMs + v.endMs) / 2
      actions.onZoom(c - half, c + half)
    } else if (e.key === '-' || e.key === '_') {
      actions.onWiden()
    } else if (e.key === 'Backspace') {
      actions.onBack()
    } else {
      handled = false
    }
    if (handled) e.preventDefault()
  }

  function updateHover(px: number): void {
    if (!view()) return
    const ms = msOfX(px)
    actions.onHover?.(clampMs(ms))
    let text: string | null = null
    for (const p of points) {
      if (p.kind !== 'stop' || !p.dwell_start || !p.dwell_end) continue
      const sMs = new Date(p.dwell_start).getTime()
      const eMs = new Date(p.dwell_end).getTime()
      if (ms >= sMs && ms <= eMs) {
        text = 'Parked ' + fmtDur(Math.round((eMs - sMs) / 60000))
        break
      }
    }
    let overAnnotation = false
    if (!text) {
      for (const a of annotations) {
        const sMs = new Date(a.start_time).getTime()
        if (a.end_time) {
          if (ms >= sMs && ms <= new Date(a.end_time).getTime()) {
            text = a.name ?? null
            overAnnotation = true
            break
          }
        } else if (Math.abs(xOfMs(sMs) - px) <= 4) {
          text = a.name ?? null
          overAnnotation = true
          break
        }
      }
    }
    canvas.style.cursor = overAnnotation && actions.onAnnotationClick ? 'pointer' : 'crosshair'
    if (text) showTooltip(text, px)
    else hideTooltip()
  }

  function showTooltip(text: string, px: number): void {
    if (!tooltip) return
    tooltip.textContent = text
    tooltip.style.left = px + 'px'
    tooltip.classList.remove('hidden')
  }

  function hideTooltip(): void {
    if (tooltip) tooltip.classList.add('hidden')
  }

  function onPointerLeave(): void {
    hideTooltip()
    actions.onHover?.(null)
  }

  canvas.addEventListener('pointerdown', onPointerDown)
  canvas.addEventListener('pointermove', onPointerMove)
  canvas.addEventListener('pointerup', onPointerUp)
  canvas.addEventListener('pointercancel', onPointerUp)
  canvas.addEventListener('pointerleave', onPointerLeave)
  canvas.addEventListener('wheel', onWheel, { passive: false })
  canvas.addEventListener('dblclick', onDblClick)
  canvas.addEventListener('keydown', onKeyDown)
  window.addEventListener('resize', draw)

  return {
    setData({ startMs, endMs, points: pts, annotations: anns }) {
      domain = { startMs, endMs }
      pending = null // a landed fetch supersedes any wheel preview
      points = pts || []
      if (anns !== undefined) annotations = anns || []
      draw()
    },
    setAnnotations(anns) {
      annotations = anns || []
      draw()
    },
    destroy() {
      if (wheelTimer) clearTimeout(wheelTimer)
      if (clickTimer) clearTimeout(clickTimer)
      canvas.removeEventListener('pointerdown', onPointerDown)
      canvas.removeEventListener('pointermove', onPointerMove)
      canvas.removeEventListener('pointerup', onPointerUp)
      canvas.removeEventListener('pointercancel', onPointerUp)
      canvas.removeEventListener('pointerleave', onPointerLeave)
      canvas.removeEventListener('wheel', onWheel)
      canvas.removeEventListener('dblclick', onDblClick)
      canvas.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('resize', draw)
    },
  }
}
