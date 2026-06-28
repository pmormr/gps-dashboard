/**
 * TimeStrip — the map view's sub-range timeline, rendered entirely in one canvas
 * (mapview-redesign S5). It owns a continuous wall-clock window domain and a
 * two-handle brush, and draws every layer in one pass: point-density coverage
 * (S4), stop dwell blocks, annotation range bands + point ticks, a dimmed mask
 * over the unselected time, and the two drag handles. Pointer Events drive the
 * brush (drag a handle to resize, drag the middle to pan, tap empty track to jump
 * the nearest handle); arrow keys nudge it. All times are ms since epoch.
 *
 * Island form: `mountTimeStrip(canvas, tooltip)` scopes to the passed elements
 * (no getElementById) and returns the control surface + a `destroy`. Kept
 * imperative — it's a custom canvas widget, not reactive UI; the Svelte timeline
 * drives it via setData/getSelection/onBrush.
 */

import type { TrackPoint } from './geo'

/** An annotation band/tick the strip overlays (the relevant subset). */
export interface StripAnnotation {
  start_time: string
  end_time?: string | null
  name?: string
}

export interface TimeStripHandle {
  setData(d: { startMs: number; endMs: number; points: TrackPoint[]; annotations?: StripAnnotation[] }): void
  setAnnotations(anns: StripAnnotation[]): void
  getSelection(): { loMs: number; hiMs: number } | null
  onBrush(fn: () => void): void
  destroy(): void
}

const HIT_PX = 20 // touch hit half-width around a handle
const EDGE = 12 // inset so full-extent handles sit off the canvas edge
const GAP_CAP_MS = 15 * 60 * 1000 // density coverage gap cap (S4)
const PAD_TOP = 5 // top lane: annotation bands + ticks
const PAD_BOT = 4 // bottom lane: stop dwell blocks

/** Mount the strip onto a canvas (+ optional tooltip el) and return its controls. */
export function mountTimeStrip(
  canvas: HTMLCanvasElement,
  tooltip: HTMLElement | null,
): TimeStripHandle {
  const ctx = canvas.getContext('2d') as CanvasRenderingContext2D
  let domain: { startMs: number; endMs: number } | null = null
  let sel: { loMs: number; hiMs: number } | null = null
  let points: TrackPoint[] = []
  let annotations: StripAnnotation[] = []
  let drag: { mode: 'lo' | 'hi' | 'pan'; startX: number; startLo: number; startHi: number } | null =
    null
  const listeners: (() => void)[] = []

  canvas.tabIndex = 0
  canvas.setAttribute('role', 'slider')
  canvas.setAttribute('aria-label', 'Time selection')

  // ── geometry ──
  const cssW = (): number => canvas.clientWidth
  const cssH = (): number => canvas.clientHeight
  const plotW = (): number => Math.max(1, cssW() - 2 * EDGE)
  const span = (): number => (domain ? domain.endMs - domain.startMs : 0)
  function xOfMs(ms: number): number {
    const s = span()
    return s > 0 && domain ? EDGE + ((ms - domain.startMs) / s) * plotW() : EDGE
  }
  function msOfX(px: number): number {
    return domain ? domain.startMs + ((px - EDGE) / plotW()) * span() : 0
  }
  function clampMs(ms: number): number {
    return domain ? Math.max(domain.startMs, Math.min(domain.endMs, ms)) : ms
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
    if (!domain || !sel || span() <= 0 || w < 1) return
    drawDensity(w, h)
    drawStops(w, h)
    drawAnnotations(w, h)
    drawSelection(w, h)
    drawHandles(w, h)
  }

  function drawDensity(w: number, h: number): void {
    if (!domain) return
    const cols = Math.max(1, Math.floor(w))
    const counts = new Float64Array(cols)
    const covered = new Uint8Array(cols)
    const colOf = (ms: number): number => Math.min(cols - 1, Math.max(0, Math.round(xOfMs(ms))))
    const fillCols = (loMs: number, hiMs: number): void => {
      const a = colOf(Math.max(loMs, domain!.startMs))
      const b = colOf(Math.min(hiMs, domain!.endMs))
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
      if (ms < domain.startMs || ms > domain.endMs) {
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

  function drawStops(_w: number, h: number): void {
    if (!domain) return
    ctx.fillStyle = 'rgba(239, 68, 68, 0.85)'
    for (const p of points) {
      if (p.kind !== 'stop' || !p.dwell_start || !p.dwell_end) continue
      const sMs = new Date(p.dwell_start).getTime()
      const eMs = new Date(p.dwell_end).getTime()
      if (eMs < domain.startMs || sMs > domain.endMs) continue
      const x0 = xOfMs(Math.max(sMs, domain.startMs))
      const x1 = xOfMs(Math.min(eMs, domain.endMs))
      ctx.fillRect(x0, h - PAD_BOT, Math.max(1.5, x1 - x0), PAD_BOT)
    }
  }

  function drawAnnotations(_w: number, _h: number): void {
    if (!domain) return
    for (const a of annotations) {
      const sMs = new Date(a.start_time).getTime()
      if (a.end_time) {
        const eMs = new Date(a.end_time).getTime()
        if (eMs < domain.startMs || sMs > domain.endMs) continue
        const x0 = xOfMs(Math.max(sMs, domain.startMs))
        const x1 = xOfMs(Math.min(eMs, domain.endMs))
        ctx.fillStyle = 'rgba(34, 211, 238, 0.65)'
        ctx.fillRect(x0, 0, Math.max(1.5, x1 - x0), 3)
      } else {
        if (sMs < domain.startMs || sMs > domain.endMs) continue
        ctx.fillStyle = '#f59e0b'
        ctx.fillRect(xOfMs(sMs) - 1, 0, 2, PAD_TOP + 4)
      }
    }
  }

  function drawSelection(w: number, h: number): void {
    if (!sel) return
    const xLo = xOfMs(sel.loMs)
    const xHi = xOfMs(sel.hiMs)
    const x0 = EDGE
    const x1 = w - EDGE
    ctx.fillStyle = 'rgba(15, 23, 42, 0.55)'
    if (xLo > x0) ctx.fillRect(x0, 0, xLo - x0, h)
    if (xHi < x1) ctx.fillRect(xHi, 0, x1 - xHi, h)
    ctx.fillStyle = 'rgba(59, 130, 246, 0.9)'
    const selW = Math.max(1, xHi - xLo)
    ctx.fillRect(xLo, 0, selW, 1.5)
    ctx.fillRect(xLo, h - 1.5, selW, 1.5)
  }

  function drawHandles(_w: number, h: number): void {
    if (!sel) return
    const knobW = 8
    const knobH = 14
    const knobY = (h - knobH) / 2
    for (const ms of [sel.loMs, sel.hiMs]) {
      const x = xOfMs(ms)
      ctx.fillStyle = '#3b82f6'
      ctx.fillRect(x - 1, 0, 2, h)
      roundRect(x - knobW / 2, knobY, knobW, knobH, 3)
      ctx.fillStyle = '#3b82f6'
      ctx.fill()
      ctx.strokeStyle = '#fff'
      ctx.lineWidth = 1.5
      ctx.stroke()
    }
  }

  function roundRect(x: number, y: number, w: number, h: number, r: number): void {
    ctx.beginPath()
    ctx.moveTo(x + r, y)
    ctx.arcTo(x + w, y, x + w, y + h, r)
    ctx.arcTo(x + w, y + h, x, y + h, r)
    ctx.arcTo(x, y + h, x, y, r)
    ctx.arcTo(x, y, x + w, y, r)
    ctx.closePath()
  }

  // ── interaction ──
  function localX(e: PointerEvent): number {
    return e.clientX - canvas.getBoundingClientRect().left
  }

  function targetAtX(px: number): 'lo' | 'hi' | 'pan' | null {
    if (!sel) return null
    const xLo = xOfMs(sel.loMs)
    const xHi = xOfMs(sel.hiMs)
    const dLo = Math.abs(px - xLo)
    const dHi = Math.abs(px - xHi)
    if (dLo <= HIT_PX && dLo <= dHi) return 'lo'
    if (dHi <= HIT_PX) return 'hi'
    if (px > xLo && px < xHi) return 'pan'
    return null
  }

  function onPointerDown(e: PointerEvent): void {
    if (!domain || !sel) return
    const px = localX(e)
    let mode = targetAtX(px)
    if (mode === null) {
      const ms = clampMs(msOfX(px))
      mode = Math.abs(px - xOfMs(sel.loMs)) <= Math.abs(px - xOfMs(sel.hiMs)) ? 'lo' : 'hi'
      if (mode === 'lo') sel.loMs = Math.min(ms, sel.hiMs)
      else sel.hiMs = Math.max(ms, sel.loMs)
      draw()
      emit()
    }
    drag = { mode, startX: px, startLo: sel.loMs, startHi: sel.hiMs }
    try {
      canvas.setPointerCapture(e.pointerId)
    } catch {
      /* not capturable — non-fatal */
    }
    hideTooltip()
    e.preventDefault()
  }

  function onPointerMove(e: PointerEvent): void {
    if (!domain || !sel) return
    const px = localX(e)
    if (!drag) {
      updateHover(px)
      return
    }
    const ms = clampMs(msOfX(px))
    if (drag.mode === 'lo') {
      sel.loMs = Math.min(ms, sel.hiMs)
    } else if (drag.mode === 'hi') {
      sel.hiMs = Math.max(ms, sel.loMs)
    } else {
      const dMs = msOfX(px) - msOfX(drag.startX)
      const width = drag.startHi - drag.startLo
      let lo = drag.startLo + dMs
      let hi = drag.startHi + dMs
      if (lo < domain.startMs) {
        lo = domain.startMs
        hi = lo + width
      }
      if (hi > domain.endMs) {
        hi = domain.endMs
        lo = hi - width
      }
      sel.loMs = lo
      sel.hiMs = hi
    }
    draw()
    emit()
    e.preventDefault()
  }

  function onPointerUp(e: PointerEvent): void {
    if (!drag) return
    drag = null
    try {
      canvas.releasePointerCapture(e.pointerId)
    } catch {
      /* non-fatal */
    }
  }

  function updateHover(px: number): void {
    if (!domain) return
    const mode = targetAtX(px)
    canvas.style.cursor =
      mode === 'lo' || mode === 'hi' ? 'ew-resize' : mode === 'pan' ? 'grab' : 'crosshair'
    const ms = msOfX(px)
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
    if (!text) {
      for (const a of annotations) {
        const sMs = new Date(a.start_time).getTime()
        if (a.end_time) {
          if (ms >= sMs && ms <= new Date(a.end_time).getTime()) {
            text = a.name ?? null
            break
          }
        } else if (Math.abs(xOfMs(sMs) - px) <= 4) {
          text = a.name ?? null
          break
        }
      }
    }
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

  function onKeyDown(e: KeyboardEvent): void {
    if (!domain || !sel) return
    const step = span() * 0.02
    const width = sel.hiMs - sel.loMs
    let handled = true
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      const dir = e.key === 'ArrowRight' ? 1 : -1
      if (e.shiftKey) {
        sel.hiMs = Math.max(sel.loMs, clampMs(sel.hiMs + dir * step))
      } else {
        let lo = sel.loMs + dir * step
        let hi = sel.hiMs + dir * step
        if (lo < domain.startMs) {
          lo = domain.startMs
          hi = lo + width
        }
        if (hi > domain.endMs) {
          hi = domain.endMs
          lo = hi - width
        }
        sel.loMs = lo
        sel.hiMs = hi
      }
    } else if (e.key === 'Home') {
      sel.loMs = domain.startMs
      sel.hiMs = domain.startMs + width
    } else if (e.key === 'End') {
      sel.hiMs = domain.endMs
      sel.loMs = domain.endMs - width
    } else {
      handled = false
    }
    if (handled) {
      draw()
      emit()
      e.preventDefault()
    }
  }

  function emit(): void {
    listeners.forEach((fn) => fn())
  }

  canvas.addEventListener('pointerdown', onPointerDown)
  canvas.addEventListener('pointermove', onPointerMove)
  canvas.addEventListener('pointerup', onPointerUp)
  canvas.addEventListener('pointercancel', onPointerUp)
  canvas.addEventListener('pointerleave', hideTooltip)
  canvas.addEventListener('keydown', onKeyDown)
  window.addEventListener('resize', draw)

  return {
    setData({ startMs, endMs, points: pts, annotations: anns }) {
      domain = { startMs, endMs }
      points = pts || []
      if (anns !== undefined) annotations = anns || []
      sel = { loMs: startMs, hiMs: endMs }
      draw()
    },
    setAnnotations(anns) {
      annotations = anns || []
      draw()
    },
    getSelection() {
      return sel ? { loMs: sel.loMs, hiMs: sel.hiMs } : null
    },
    onBrush(fn) {
      listeners.push(fn)
    },
    destroy() {
      canvas.removeEventListener('pointerdown', onPointerDown)
      canvas.removeEventListener('pointermove', onPointerMove)
      canvas.removeEventListener('pointerup', onPointerUp)
      canvas.removeEventListener('pointercancel', onPointerUp)
      canvas.removeEventListener('pointerleave', hideTooltip)
      canvas.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('resize', draw)
    },
  }
}
