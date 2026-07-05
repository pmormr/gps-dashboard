import { describe, expect, it } from 'vitest'

import { angleDelta, clamp01, gateHeading, HEADING_GATE_MS, lerpAngle, lerpPos } from './live'

describe('gateHeading', () => {
  it('passes track through at driving speed', () => {
    expect(gateHeading(90, 180, 10)).toBe(180)
  })

  it('holds the previous heading below the gate (stoplight spin)', () => {
    expect(gateHeading(90, 271, 0.3)).toBe(90)
    expect(gateHeading(90, 271, HEADING_GATE_MS - 0.01)).toBe(90)
  })

  it('accepts exactly at the gate', () => {
    expect(gateHeading(90, 271, HEADING_GATE_MS)).toBe(271)
  })

  it('holds on missing track or speed', () => {
    expect(gateHeading(90, null, 10)).toBe(90)
    expect(gateHeading(90, 180, null)).toBe(90)
  })

  it('stays null before any good reading', () => {
    expect(gateHeading(null, 180, 0.2)).toBeNull()
    expect(gateHeading(null, null, 10)).toBeNull()
  })
})

describe('angleDelta', () => {
  it('takes the short way across north', () => {
    expect(angleDelta(350, 10)).toBe(20)
    expect(angleDelta(10, 350)).toBe(-20)
  })

  it('handles the 180 boundary', () => {
    expect(angleDelta(0, 180)).toBe(180)
    expect(angleDelta(180, 0)).toBe(180)
  })

  it('is zero for equal bearings', () => {
    expect(angleDelta(45, 45)).toBe(0)
  })
})

describe('lerpAngle', () => {
  it('interpolates across north without unwinding', () => {
    expect(lerpAngle(350, 10, 0.5)).toBe(0)
    expect(lerpAngle(10, 350, 0.5)).toBe(0)
  })

  it('normalizes into [0, 360)', () => {
    const a = lerpAngle(350, 10, 0.25)
    expect(a).toBeGreaterThanOrEqual(0)
    expect(a).toBeLessThan(360)
    expect(a).toBeCloseTo(355)
  })

  it('hits the endpoints', () => {
    expect(lerpAngle(90, 180, 0)).toBe(90)
    expect(lerpAngle(90, 180, 1)).toBe(180)
  })
})

describe('lerpPos', () => {
  it('interpolates linearly', () => {
    const p = lerpPos({ lat: 39, lon: -105 }, { lat: 40, lon: -104 }, 0.5)
    expect(p.lat).toBeCloseTo(39.5)
    expect(p.lon).toBeCloseTo(-104.5)
  })
})

describe('clamp01', () => {
  it('clamps both ends and passes the middle', () => {
    expect(clamp01(-0.5)).toBe(0)
    expect(clamp01(0.7)).toBe(0.7)
    expect(clamp01(1.5)).toBe(1)
  })
})
