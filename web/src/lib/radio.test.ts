import { describe, expect, it } from 'vitest'

import { sMeter } from './radio'

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
