/**
 * The global time-axis Selection store.
 *
 * Every historical-window read in the app takes a `start`/`end` (canonical ms-UTC
 * is uniform across tiers), so one window drives many consumers. This store is
 * that single window: the Graylog-style picker state (mode/anchor/window/live, the
 * *fetch* window) plus the sub-window *brush* within the loaded window. Consumers
 * react to `range` (refetch their own data) and `brush` (highlight the sub-window);
 * the map is the first consumer (see Timeline.svelte), sensors/globe adopt later.
 *
 * `live` is a mode of the axis — anchor follows now and a 30s tick makes `range`
 * recompute so consumers refresh. Ported from the imperative TimePicker singleton.
 */

export type Mode = 'last' | 'around' | 'range'

/** The derived fetch window for the current picker state. */
export interface Range {
  from: Date
  to: Date
  live: boolean
  mode: Mode
  windowMs: number
}

/** A sub-window brush within the loaded window (ms epoch). */
export interface Brush {
  loMs: number
  hiMs: number
}

/** A snapshot of the picker fields — enough to restore a window verbatim (zoom-out). */
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

/** Preset windows shown as chips in the picker. */
export const PRESETS: { label: string; ms: number }[] = [
  { label: '15m', ms: 15 * 60 * 1000 },
  { label: '1h', ms: 60 * 60 * 1000 },
  { label: '6h', ms: 6 * 60 * 60 * 1000 },
  { label: '24h', ms: 24 * 60 * 60 * 1000 },
  { label: '7d', ms: 7 * 24 * 60 * 60 * 1000 },
  { label: '30d', ms: 30 * 24 * 60 * 60 * 1000 },
]

class SelectionStore {
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

  // The loaded window (set when a fetch lands) — the strip domain + brush bounds.
  loadedFrom = $state<number | null>(null)
  loadedTo = $state<number | null>(null)
  // The brush sub-window within the loaded window (mirrors the TimeStrip handles).
  brushLo = $state<number | null>(null)
  brushHi = $state<number | null>(null)

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
    if (this.mode === 'last') {
      return {
        from: new Date(nowMs - this.windowMs),
        to: new Date(nowMs),
        live: this.live,
        mode: 'last',
        windowMs: this.windowMs,
      }
    }
    return {
      from: new Date(nowMs - this.windowMs / 2),
      to: new Date(nowMs + this.windowMs / 2),
      live: false,
      mode: 'around',
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

  /** The current brush, or null before the first load. */
  get brush(): Brush | null {
    return this.brushLo != null && this.brushHi != null
      ? { loMs: this.brushLo, hiMs: this.brushHi }
      : null
  }

  /** Whether the brush is a strict sub-range of the loaded window. */
  get isSubRange(): boolean {
    if (this.loadedFrom == null || this.loadedTo == null || this.brushLo == null || this.brushHi == null) {
      return false
    }
    return this.brushLo > this.loadedFrom || this.brushHi < this.loadedTo
  }

  /** Human label for the picker trigger. */
  get label(): string {
    if (this.mode === 'range') {
      if (!this.from || !this.to) return 'From → To'
      return `${fmtAnchor(this.from)} → ${fmtAnchor(this.to)}`
    }
    const win = fmtDuration(this.windowMs)
    if (this.mode === 'last') {
      return this.live
        ? `Live · Last ${win}`
        : `Last ${win} ending ${fmtAnchor(this.anchor ?? new Date())}`
    }
    return `Around ${fmtAnchor(this.anchor ?? new Date())} ±${fmtDuration(this.windowMs / 2)}`
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

  /** Jump to an explicit range (zoom-to-range, use-marks). */
  setRange(from: Date, to: Date): void {
    this.setPicker({ mode: 'range', from, to, live: false })
  }

  /** Record the window a fetch loaded, resetting the brush to the full window. */
  setLoaded(fromMs: number, toMs: number): void {
    this.loadedFrom = fromMs
    this.loadedTo = toMs
    this.brushLo = fromMs
    this.brushHi = toMs
  }

  /** Update the brush sub-window (from the TimeStrip). */
  setBrush(loMs: number, hiMs: number): void {
    this.brushLo = loMs
    this.brushHi = hiMs
  }

  private updatePolling(): void {
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
