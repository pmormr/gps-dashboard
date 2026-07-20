// Pure helpers for the trend charts — smoothing + domain/axis math, kept out of
// the Svelte layer components so they're unit-testable and shared across layers.

/** A trend series reduced to what the chart needs: a metric, its style, its data. */
export interface PlotSeries {
  metric: string
  label: string
  unit: string
  color: string
  dec: number
  /** Smoothed value per bucket, aligned to the shared x grid; null = gap. */
  values: (number | null)[]
}

export type AxisSide = 'left' | 'right'

/**
 * Centered moving average over `window` buckets, skipping nulls.
 *
 * `window <= 1` returns a copy (no smoothing). A bucket whose window holds no
 * non-null samples stays null, so genuine gaps survive smoothing.
 */
export function movingAverage(values: (number | null)[], window: number): (number | null)[] {
  if (window <= 1) return values.slice()
  const half = Math.floor(window / 2)
  const out: (number | null)[] = new Array(values.length).fill(null)
  for (let i = 0; i < values.length; i++) {
    let sum = 0
    let n = 0
    for (let j = Math.max(0, i - half); j <= Math.min(values.length - 1, i + half); j++) {
      const v = values[j]
      if (v != null) {
        sum += v
        n++
      }
    }
    if (n > 0) out[i] = sum / n
  }
  return out
}

/**
 * Map a plot-area pixel x to a time (epoch ms), clamped to `[startMs, endMs]`.
 *
 * Inverts the chart's linear time scale (domain `[startMs, endMs]` over the plot
 * width `[padLeft, padLeft + plotW]`) — the basis for drag-to-zoom region select.
 */
export function pixelToTime(
  mx: number,
  padLeft: number,
  plotW: number,
  startMs: number,
  endMs: number
): number {
  if (plotW <= 0) return startMs
  const frac = Math.min(1, Math.max(0, (mx - padLeft) / plotW))
  return startMs + frac * (endMs - startMs)
}

/**
 * Group defined samples into connected segments, returning index arrays into the
 * dense grid.
 *
 * The server returns a dense bucket grid with nulls for empty buckets; a line
 * that breaks at every null shatters once the buckets are finer than the data's
 * cadence (zooming into engine-gated data). So a break is inserted only where the
 * time gap between consecutive defined samples exceeds ``gapFactor`` times the
 * *median* sample spacing — small empty-bucket runs (a bucketing artefact) stay
 * connected, while a genuine no-data gap still splits the line. A run of one
 * index is a lone sample (drawn as a dot, not a segment).
 *
 * Args:
 *   times: Per-bucket epoch-ms, aligned to ``defined``.
 *   defined: Whether each bucket has a value to plot.
 *   gapFactor: Break threshold as a multiple of the median spacing.
 *
 * Returns:
 *   Connected runs of defined indices, in time order.
 */
export function lineSegments(
  times: number[],
  defined: boolean[],
  gapFactor = 8
): number[][] {
  const idx: number[] = []
  for (let i = 0; i < defined.length; i++) if (defined[i]) idx.push(i)
  if (idx.length === 0) return []
  const gaps: number[] = []
  for (let i = 1; i < idx.length; i++) gaps.push(times[idx[i]] - times[idx[i - 1]])
  let breakAt = Infinity
  if (gaps.length) {
    const sorted = [...gaps].sort((a, b) => a - b)
    const median = sorted[Math.floor(sorted.length / 2)]
    if (median > 0) breakAt = median * gapFactor
  }
  const segs: number[][] = []
  let cur: number[] = [idx[0]]
  for (let i = 1; i < idx.length; i++) {
    if (times[idx[i]] - times[idx[i - 1]] > breakAt) {
      segs.push(cur)
      cur = []
    }
    cur.push(idx[i])
  }
  segs.push(cur)
  return segs
}

/** [min, max] over non-null values, or null when every value is null. */
export function extent(values: (number | null)[]): [number, number] | null {
  let lo = Infinity
  let hi = -Infinity
  for (const v of values) {
    if (v == null) continue
    if (v < lo) lo = v
    if (v > hi) hi = v
  }
  return lo === Infinity ? null : [lo, hi]
}

/** Union of several extents (skipping nulls), or null when all are null. */
export function unionExtent(extents: ([number, number] | null)[]): [number, number] | null {
  let lo = Infinity
  let hi = -Infinity
  for (const e of extents) {
    if (e == null) continue
    if (e[0] < lo) lo = e[0]
    if (e[1] > hi) hi = e[1]
  }
  return lo === Infinity ? null : [lo, hi]
}

/** Pad a [min, max] domain by `frac` each side; a flat domain gets unit headroom. */
export function padDomain([lo, hi]: [number, number], frac = 0.08): [number, number] {
  if (lo === hi) return [lo - 1, hi + 1]
  const p = (hi - lo) * frac
  return [lo - p, hi + p]
}

/**
 * Build an SVG path for a compact sparkline over a value grid.
 *
 * Values are the dense bucket grid `/api/sensors/series` returns (nulls for empty
 * buckets); the line bridges nulls (a 22 px glance sparkline reads better
 * continuous than shattered — honest gaps are the full Trends chart's job). x is
 * the value's index fraction across the full grid, so leading/trailing gaps show
 * as empty margin. y normalizes over the non-null min/max; a flat series sits at
 * mid-height. Returns '' for fewer than two plottable points (the caller draws
 * nothing).
 *
 * Args:
 *   values: Per-bucket values aligned to the grid; null = empty bucket.
 *   width: Path coordinate width (viewBox units).
 *   height: Path coordinate height (viewBox units).
 *   pad: Vertical inset so the stroke isn't clipped at the extremes.
 */
export function sparklinePath(
  values: (number | null)[],
  width: number,
  height: number,
  pad = 2
): string {
  const pts: { i: number; v: number }[] = []
  values.forEach((v, i) => {
    if (v != null && Number.isFinite(v)) pts.push({ i, v })
  })
  if (pts.length < 2) return ''
  const n = values.length
  const vs = pts.map((p) => p.v)
  const min = Math.min(...vs)
  const span = Math.max(...vs) - min
  const h = height - pad * 2
  const x = (i: number): number => (n <= 1 ? 0 : (i / (n - 1)) * width)
  const y = (v: number): number => (span === 0 ? height / 2 : pad + h - ((v - min) / span) * h)
  return pts.map((p, k) => `${k === 0 ? 'M' : 'L'}${x(p.i).toFixed(1)} ${y(p.v).toFixed(1)}`).join(' ')
}

/**
 * Assign each distinct unit to an axis side.
 *
 * First unit → left, second → right; any further units share the left axis —
 * visible axes are capped at two. Empty-string units (unitless metrics) are
 * treated as their own group.
 */
export function axisForUnits(units: string[]): Map<string, AxisSide> {
  const seen: string[] = []
  for (const u of units) if (!seen.includes(u)) seen.push(u)
  const sides = new Map<string, AxisSide>()
  seen.forEach((u, i) => sides.set(u, i === 1 ? 'right' : 'left'))
  return sides
}
