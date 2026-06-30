import { describe, expect, it } from 'vitest'

import { axisForUnits, extent, movingAverage, padDomain, unionExtent } from './util'

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
