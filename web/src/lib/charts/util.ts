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
 * Assign each distinct unit to an axis side.
 *
 * First unit → left, second → right; any further units share the left axis.
 * Phase 1 caps visible axes at two (true multi-axis is Phase 2). Empty-string
 * units (unitless metrics) are treated as their own group.
 */
export function axisForUnits(units: string[]): Map<string, AxisSide> {
  const seen: string[] = []
  for (const u of units) if (!seen.includes(u)) seen.push(u)
  const sides = new Map<string, AxisSide>()
  seen.forEach((u, i) => sides.set(u, i === 1 ? 'right' : 'left'))
  return sides
}
