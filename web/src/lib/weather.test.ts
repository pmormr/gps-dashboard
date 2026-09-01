import { describe, expect, it } from 'vitest'

import { decimateFrames, formatBytes, frameAgeLabel } from './weather'

describe('decimateFrames', () => {
  const uniform = (n: number, stepMs = 420_000, start = 1_788_000_000_000): number[] =>
    Array.from({ length: n }, (_, i) => start + i * stepMs)

  it('returns everything when under budget', () => {
    const frames = uniform(50)
    expect(decimateFrames(frames, 90)).toEqual(frames)
  })

  it('thins to at most the budget, evenly, keeping newest and oldest', () => {
    const frames = uniform(2900)
    const out = decimateFrames(frames, 84)
    expect(out.length).toBeLessThanOrEqual(84)
    expect(out.length).toBeGreaterThan(80)
    expect(out[0]).toBe(frames[0])
    expect(out[out.length - 1]).toBe(frames[frames.length - 1])
    const gaps = out.slice(1).map((f, i) => f - out[i])
    const target = (frames[frames.length - 1] - frames[0]) / 83
    for (const g of gaps) expect(Math.abs(g - target)).toBeLessThan(target)
  })

  it('stays ascending and deduped across archive gaps', () => {
    // A 3-day hole in the middle: targets falling inside collapse to its edges.
    const frames = [...uniform(500), ...uniform(500, 420_000, 1_788_500_000_000)]
    const out = decimateFrames(frames, 60)
    expect(out.length).toBeLessThanOrEqual(60)
    expect([...out].sort((a, b) => a - b)).toEqual(out)
    expect(new Set(out).size).toBe(out.length)
  })

  it('handles empty input', () => {
    expect(decimateFrames([], 84)).toEqual([])
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
