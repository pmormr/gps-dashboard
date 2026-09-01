import { describe, expect, it } from 'vitest'

import { formatBytes, frameAgeLabel, needsRecenter, sliceRange } from './weather'

describe('sliceRange', () => {
  it('covers everything when the list fits the cap', () => {
    expect(sliceRange(30, 29, 48)).toEqual({ start: 0, end: 30 })
    expect(sliceRange(0, 0, 48)).toEqual({ start: 0, end: 0 })
  })

  it('centers the cap on the index', () => {
    expect(sliceRange(2900, 1000, 48)).toEqual({ start: 976, end: 1024 })
  })

  it('clamps at both ends', () => {
    expect(sliceRange(2900, 3, 48)).toEqual({ start: 0, end: 48 })
    expect(sliceRange(2900, 2899, 48)).toEqual({ start: 2852, end: 2900 })
  })
})

describe('needsRecenter', () => {
  const range = { start: 100, end: 148 }

  it('is false comfortably inside the range', () => {
    expect(needsRecenter(range, 2900, 124, 8)).toBe(false)
  })

  it('is true outside the range', () => {
    expect(needsRecenter(range, 2900, 99, 8)).toBe(true)
    expect(needsRecenter(range, 2900, 148, 8)).toBe(true)
  })

  it('is true within the margin of an edge with frames beyond it', () => {
    expect(needsRecenter(range, 2900, 105, 8)).toBe(true)
    expect(needsRecenter(range, 2900, 141, 8)).toBe(true)
  })

  it('ignores edges the list actually ends at', () => {
    expect(needsRecenter({ start: 0, end: 48 }, 2900, 3, 8)).toBe(false)
    expect(needsRecenter({ start: 2852, end: 2900 }, 2900, 2897, 8)).toBe(false)
    // The whole list loaded: never recenters.
    expect(needsRecenter({ start: 0, end: 30 }, 30, 0, 8)).toBe(false)
    expect(needsRecenter({ start: 0, end: 30 }, 30, 29, 8)).toBe(false)
  })
})

describe('formatBytes', () => {
  it('renders bytes below 1 kB', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1023)).toBe('1023 B')
  })

  it('renders kB below 1 MB', () => {
    expect(formatBytes(1024)).toBe('1 kB')
    expect(formatBytes(22_000)).toBe('21 kB')
    expect(formatBytes(1_048_575)).toBe('1024 kB')
  })

  it('renders MB with one decimal below 10 MB, whole above', () => {
    expect(formatBytes(1_048_576)).toBe('1.0 MB')
    expect(formatBytes(2_700_000)).toBe('2.6 MB')
    expect(formatBytes(10_485_760)).toBe('10 MB')
    expect(formatBytes(52_428_800)).toBe('50 MB')
  })
})

describe('frameAgeLabel', () => {
  const now = 1_788_270_000_000

  it('reads "now" under a minute', () => {
    expect(frameAgeLabel(now - 30_000, now)).toBe('now')
  })

  it('reads minutes under an hour', () => {
    expect(frameAgeLabel(now - 9 * 60_000, now)).toBe('9 min ago')
  })

  it('reads hours + minutes past an hour', () => {
    expect(frameAgeLabel(now - 90 * 60_000, now)).toBe('1h 30m ago')
    expect(frameAgeLabel(now - 120 * 60_000, now)).toBe('2h ago')
  })
})
