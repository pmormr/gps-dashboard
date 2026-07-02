/**
 * The global time-axis Selection store.
 *
 * Every historical-window read in the app takes a `start`/`end` (canonical ms-UTC
 * is uniform across tiers), so one window drives many consumers. This store is
 * that single window — the picker state (mode/anchor/window/live, the *fetch*
 * window) — plus a zoom history so drag-to-zoom anywhere can step back out.
 * The window is the only time object (time-dock plan): consumers react to
 * `range` and refetch; Map and Trends share the same axis.
 *
 * `live` is a mode of the axis — anchor follows now and a 30s tick makes `range`
 * recompute so consumers refresh. `zoomTo` forces live off (it's a `setRange`),
 * but the pushed snapshot preserves `live: true`, so `back()` out of a zoom
 * *resumes* Live — intended.
 */

export type Mode = 'last' | 'range'

/** The derived fetch window for the current picker state. */
export interface Range {
  from: Date
  to: Date
  live: boolean
  mode: Mode
  windowMs: number
}

/** A snapshot of the picker fields — enough to restore a window verbatim (back()). */
export interface PickerState {
  mode: Mode
  anchor: Date | null
  windowMs: number
  from: Date | null
  to: Date | null
  live: boolean
}

const DEFAULT_WINDOW_MS = 24 * 60 * 60 * 1000
const POLL_MS = 30 * 1000
// Window cap for widen() and the strip's wheel zoom-out — a year still reaches an
// all-history-ish view in a few doublings, and back() steps out of it instantly.
export const MAX_WINDOW_MS = 365 * 24 * 60 * 60 * 1000

/** Preset windows shown as chips in the picker. */
export const PRESETS: { label: string; ms: number }[] = [
  { label: '15m', ms: 15 * 60 * 1000 },
  { label: '1h', ms: 60 * 60 * 1000 },
  { label: '6h', ms: 6 * 60 * 60 * 1000 },
  { label: '24h', ms: 24 * 60 * 60 * 1000 },
  { label: '7d', ms: 7 * 24 * 60 * 60 * 1000 },
  { label: '30d', ms: 30 * 24 * 60 * 60 * 1000 },
]

export class SelectionStore {
  // Picker state — the source of truth for the fetch window.
  mode = $state<Mode>('last')
  anchor = $state<Date | null>(null)
  windowMs = $state(DEFAULT_WINDOW_MS)
  from = $state<Date | null>(null)
  to = $state<Date | null>(null)
  live = $state(true)

  // Bumped every POLL_MS while live so `range` recomputes against a fresh `now`
  // and consumers re-fetch (the reactive replacement for the old emit timer).
  private tick = $state(0)
  private pollTimer: number | undefined

  // Zoom history — picker snapshots pushed by `zoomTo`, popped by `back`.
  private zoomStack = $state<PickerState[]>([])

  constructor() {
    this.updatePolling()
  }

  /** The derived fetch window. Reactive: depends on the picker fields (+ the live tick). */
  get range(): Range {
    if (this.mode === 'range' && this.from && this.to) {
      return {
        from: this.from,
        to: this.to,
        live: false,
        mode: 'range',
        windowMs: this.to.getTime() - this.from.getTime(),
      }
    }
    let nowMs: number
    if (this.live) {
      void this.tick // tie live ranges to the 30s poll so consumers refresh
      nowMs = Date.now()
    } else {
      nowMs = this.anchor?.getTime() ?? Date.now()
    }
    return {
      from: new Date(nowMs - this.windowMs),
      to: new Date(nowMs),
      live: this.live,
      mode: 'last',
      windowMs: this.windowMs,
    }
  }

  /** A snapshot of the picker fields — push before a zoom, pass to `setPicker` to restore. */
  get pickerState(): PickerState {
    return {
      mode: this.mode,
      anchor: this.anchor,
      windowMs: this.windowMs,
      from: this.from,
      to: this.to,
      live: this.live,
    }
  }

  /** Whether `back()` has a window to step out to. */
  get canGoBack(): boolean {
    return this.zoomStack.length > 0
  }

  /** Human label for the picker trigger. */
  get label(): string {
    if (this.mode === 'range') {
      if (!this.from || !this.to) return 'From → To'
      return `${fmtAnchor(this.from)} → ${fmtAnchor(this.to)}`
    }
    const win = fmtDuration(this.windowMs)
    return this.live
      ? `Live · Last ${win}`
      : `Last ${win} ending ${fmtAnchor(this.anchor ?? new Date())}`
  }

  /** Apply a picker change (live is forced off outside `last`); restarts polling. */
  setPicker(p: {
    mode?: Mode
    anchor?: Date | null
    windowMs?: number
    from?: Date | null
    to?: Date | null
    live?: boolean
  }): void {
    if (p.mode !== undefined) this.mode = p.mode
    if (p.anchor !== undefined) this.anchor = p.anchor
    if (p.windowMs !== undefined) this.windowMs = p.windowMs
    if (p.from !== undefined) this.from = p.from
    if (p.to !== undefined) this.to = p.to
    if (p.live !== undefined) this.live = p.live
    if (this.mode !== 'last') this.live = false
    this.updatePolling()
  }

  /** Jump to an explicit range (absolute picker jump, use-marks, annotation jump). */
  setRange(from: Date, to: Date): void {
    this.setPicker({ mode: 'range', from, to, live: false })
  }

  /** Narrow to a sub-window (drag-zoom), pushing the current window onto the history. */
  zoomTo(from: Date, to: Date): void {
    this.zoomStack = [...this.zoomStack, this.pickerState]
    this.setRange(from, to)
  }

  /** Step back out to the window before the last `zoomTo`. No-op on an empty stack. */
  back(): void {
    const prev = this.zoomStack.at(-1)
    if (!prev) return
    this.zoomStack = this.zoomStack.slice(0, -1)
    this.setPicker(prev)
  }

  /** Pop the whole history — restore the window before the first `zoomTo`. */
  resetZoom(): void {
    const base = this.zoomStack[0]
    if (!base) return
    this.zoomStack = []
    this.setPicker(base)
  }

  /**
   * Move the window by one window-width. Shifting back leaves Live (the anchor
   * freezes); shifting forward while Live is a no-op — the window already ends
   * at now.
   */
  shift(dir: -1 | 1): void {
    if (this.live && dir === 1) return
    const { from, to, windowMs } = this.range
    const newFrom = new Date(from.getTime() + dir * windowMs)
    const newTo = new Date(to.getTime() + dir * windowMs)
    if (this.mode === 'range') this.setRange(newFrom, newTo)
    else this.setPicker({ mode: 'last', anchor: newTo, live: false })
  }

  /**
   * Double the window around its center, capped at a year. Live keeps following
   * now (the window just extends further back — half a live window in the future
   * would be empty).
   */
  widen(): void {
    const { from, to, windowMs } = this.range
    const newW = Math.min(windowMs * 2, MAX_WINDOW_MS)
    if (newW === windowMs) return
    if (this.live) {
      this.setPicker({ mode: 'last', windowMs: newW, live: true })
      return
    }
    const centerMs = (from.getTime() + to.getTime()) / 2
    if (this.mode === 'range') {
      this.setRange(new Date(centerMs - newW / 2), new Date(centerMs + newW / 2))
    } else {
      this.setPicker({ mode: 'last', anchor: new Date(centerMs + newW / 2), windowMs: newW, live: false })
    }
  }

  private updatePolling(): void {
    if (typeof window === 'undefined') return // non-DOM (unit tests) — no live poll
    if (this.pollTimer) {
      clearInterval(this.pollTimer)
      this.pollTimer = undefined
    }
    if (this.live) this.pollTimer = window.setInterval(() => (this.tick += 1), POLL_MS)
  }
}

/** Local short datetime for labels. */
function fmtAnchor(d: Date): string {
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

/** Compact duration label — preset labels first (so 24h reads "24h", not "1d"). */
export function fmtDuration(ms: number): string {
  const p = PRESETS.find((x) => x.ms === ms)
  if (p) return p.label
  if (ms % 86400000 === 0) return `${ms / 86400000}d`
  if (ms % 3600000 === 0) return `${ms / 3600000}h`
  return `${Math.round(ms / 60000)}m`
}

export const selection = new SelectionStore()
