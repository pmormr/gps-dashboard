import { describe, expect, it } from 'vitest'

import { barsPath, cursorX, parseWaveform, seekTime, sMeter } from './radio'

describe('sMeter', () => {
  it('returns null for missing readings', () => {
    expect(sMeter(null)).toBeNull()
    expect(sMeter(undefined)).toBeNull()
  })

  it('anchors the manual scale: 0=S0, 170=S9', () => {
    expect(sMeter(0)?.label).toBe('S0')
    expect(sMeter(170)?.label).toBe('S9')
  })

  it('floors between units like a segment display', () => {
    expect(sMeter(169)?.label).toBe('S8')
    expect(sMeter(19)?.label).toBe('S1') // 170/9 ≈ 18.9 raw per unit
    expect(sMeter(18)?.label).toBe('S0')
  })

  it('labels anything past S9 as S9+', () => {
    expect(sMeter(171)?.label).toBe('S9+')
    expect(sMeter(255)?.label).toBe('S9+')
  })

  it('clamps out-of-range input and scales the bar over the full 0–255', () => {
    expect(sMeter(-5)).toEqual({ label: 'S0', pct: 0 })
    expect(sMeter(300)).toEqual({ label: 'S9+', pct: 100 })
    expect(sMeter(170)?.pct).toBeCloseTo((170 / 255) * 100)
  })
})

describe('parseWaveform', () => {
  it('coerces null/undefined to an empty array', () => {
    expect(parseWaveform(null)).toEqual([])
    expect(parseWaveform(undefined)).toEqual([])
  })

  it('normalizes stored 0..255 heights to 0..1 fractions', () => {
    expect(parseWaveform([0, 255, 128])).toEqual([0, 1, 128 / 255])
  })

  it('clamps stray out-of-range values into [0, 1]', () => {
    expect(parseWaveform([-10, 300])).toEqual([0, 1])
  })
})

describe('barsPath', () => {
  it('is empty for no samples', () => {
    expect(barsPath([])).toBe('')
  })

  it('emits one bar subpath per sample', () => {
    const d = barsPath([0.5, 0.5, 0.5])
    expect((d.match(/M/g) ?? []).length).toBe(3)
  })

  it('spans full height for a max sample and centers it', () => {
    // sample 1 → h=100, y=(100-100)/2=0.
    expect(barsPath([1])).toBe('M0.1 0.00h0.8v100.00h-0.8z')
  })

  it('floors silence to a hairline so the strip stays continuous', () => {
    // sample 0 → h=2 (the floor), y=49.
    expect(barsPath([0])).toBe('M0.1 49.00h0.8v2.00h-0.8z')
  })
})

describe('cursorX', () => {
  it('maps play time to a pixel offset', () => {
    expect(cursorX(5, 10, 200)).toBe(100)
    expect(cursorX(0, 10, 200)).toBe(0)
    expect(cursorX(10, 10, 200)).toBe(200)
  })

  it('clamps past the ends and guards a zero duration', () => {
    expect(cursorX(15, 10, 200)).toBe(200)
    expect(cursorX(5, 0, 200)).toBe(0)
  })
})

describe('seekTime', () => {
  it('maps a click x to a seek time (inverse of cursorX)', () => {
    expect(seekTime(100, 200, 10)).toBe(5)
    expect(seekTime(0, 200, 10)).toBe(0)
    expect(seekTime(200, 200, 10)).toBe(10)
  })

  it('clamps past the ends and guards a zero width', () => {
    expect(seekTime(250, 200, 10)).toBe(10)
    expect(seekTime(50, 0, 10)).toBe(0)
  })
})
