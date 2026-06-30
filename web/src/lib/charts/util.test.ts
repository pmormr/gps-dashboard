import { describe, expect, it } from 'vitest'

import {
  axisForUnits,
  extent,
  lineSegments,
  movingAverage,
  padDomain,
  pixelToTime,
  unionExtent,
} from './util'

describe('movingAverage', () => {
  it('returns a copy for window <= 1 (no smoothing)', () => {
    const v = [1, 2, null, 4]
    expect(movingAverage(v, 1)).toEqual(v)
    expect(movingAverage(v, 1)).not.toBe(v)
  })

  it('centers the average over the window', () => {
    expect(movingAverage([1, 2, 3, 4, 5], 3)).toEqual([1.5, 2, 3, 4, 4.5])
  })

  it('skips nulls within the window', () => {
    expect(movingAverage([2, null, 4], 3)).toEqual([2, 3, 4])
  })

  it('keeps a bucket null when its whole window is null (gaps survive)', () => {
    expect(movingAverage([null, null, null], 3)).toEqual([null, null, null])
  })
})

describe('extent / unionExtent', () => {
  it('ignores nulls', () => {
    expect(extent([1, null, 3])).toEqual([1, 3])
  })

  it('is null when every value is null', () => {
    expect(extent([null, null])).toBeNull()
  })

  it('unions several extents, skipping nulls', () => {
    expect(unionExtent([[1, 3], null, [2, 5]])).toEqual([1, 5])
  })

  it('unions to null when all are null', () => {
    expect(unionExtent([null, null])).toBeNull()
  })
})

describe('padDomain', () => {
  it('pads each side by the fraction', () => {
    expect(padDomain([0, 10])).toEqual([-0.8, 10.8])
  })

  it('gives a flat domain unit headroom', () => {
    expect(padDomain([5, 5])).toEqual([4, 6])
  })
})

describe('lineSegments', () => {
  const T = (n: number): number[] => Array.from({ length: n }, (_, i) => i * 1000)

  it('keeps evenly-spaced samples in one segment', () => {
    expect(lineSegments(T(4), [true, true, true, true])).toEqual([[0, 1, 2, 3]])
  })

  it('bridges small empty-bucket runs (a bucketing artefact)', () => {
    // gaps 1s,1s among dense buckets → median 1s → threshold 8s; the 1-bucket
    // hole (2s gap) stays connected.
    expect(lineSegments(T(5), [true, true, false, true, true])).toEqual([[0, 1, 3, 4]])
  })

  it('breaks on a gap far larger than the median spacing', () => {
    // 0,1,2s then a jump to 100s: median 1s, threshold 8s, 98s gap splits.
    const times = [0, 1000, 2000, 100000, 101000]
    expect(lineSegments(times, [true, true, true, true, true])).toEqual([[0, 1, 2], [3, 4]])
  })

  it('connects sparse-but-regular samples (the zoom-in case)', () => {
    // 30s spacing everywhere → median 30s → threshold 240s → all connected.
    const times = [0, 30000, 60000, 90000]
    expect(lineSegments(times, [true, true, true, true])).toEqual([[0, 1, 2, 3]])
  })

  it('returns a lone sample as a singleton run', () => {
    const times = [0, 1000, 2000, 3000, 1000000]
    expect(lineSegments(times, [true, true, true, true, true])).toEqual([[0, 1, 2, 3], [4]])
  })

  it('returns nothing when no bucket is defined', () => {
    expect(lineSegments(T(3), [false, false, false])).toEqual([])
    expect(lineSegments([], [])).toEqual([])
  })
})

describe('pixelToTime', () => {
  // Plot spans x∈[40, 240] (padLeft 40, plotW 200) over t∈[1000, 5000].
  it('maps the left edge to the start and the right edge to the end', () => {
    expect(pixelToTime(40, 40, 200, 1000, 5000)).toBe(1000)
    expect(pixelToTime(240, 40, 200, 1000, 5000)).toBe(5000)
  })

  it('maps the midpoint linearly', () => {
    expect(pixelToTime(140, 40, 200, 1000, 5000)).toBe(3000)
  })

  it('clamps pixels outside the plot to the domain bounds', () => {
    expect(pixelToTime(0, 40, 200, 1000, 5000)).toBe(1000)
    expect(pixelToTime(999, 40, 200, 1000, 5000)).toBe(5000)
  })

  it('returns the start for a degenerate (zero-width) plot', () => {
    expect(pixelToTime(140, 40, 0, 1000, 5000)).toBe(1000)
  })
})

describe('axisForUnits', () => {
  it('assigns first unit left, second right, rest left (two-axis cap)', () => {
    const sides = axisForUnits(['V', 'A', 'W'])
    expect(sides.get('V')).toBe('left')
    expect(sides.get('A')).toBe('right')
    expect(sides.get('W')).toBe('left')
  })

  it('de-duplicates repeated units onto one side', () => {
    const sides = axisForUnits(['V', 'V'])
    expect(sides.size).toBe(1)
    expect(sides.get('V')).toBe('left')
  })
})
