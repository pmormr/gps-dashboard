import { describe, expect, it } from 'vitest'

import {
  angleDelta,
  cardinal,
  clamp01,
  type Crumb,
  CRUMB_MAX_AGE_MS,
  CRUMB_MAX_COUNT,
  extendCrumbs,
  gateHeading,
  HEADING_GATE_MS,
  lerpAngle,
  lerpPos,
} from './live'
import { haversineMeters } from './geo'

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

describe('cardinal', () => {
  it('maps the principal winds', () => {
    expect(cardinal(0)).toBe('N')
    expect(cardinal(90)).toBe('E')
    expect(cardinal(180)).toBe('S')
    expect(cardinal(270)).toBe('W')
  })

  it('rounds to the nearest of 16 winds and wraps', () => {
    expect(cardinal(22)).toBe('NNE')
    expect(cardinal(354)).toBe('N')
    expect(cardinal(360)).toBe('N')
    expect(cardinal(-45)).toBe('NW')
  })
})

describe('extendCrumbs', () => {
  // ~1e-4 deg lat ≈ 11 m — comfortably past the 5 m gate.
  const STEP = 1e-4

  function trail(n: number, t0 = 0): Crumb[] {
    return Array.from({ length: n }, (_, i) => ({ lat: 40 + i * STEP, lon: -105, t: t0 + i * 1000 }))
  }

  it('appends a moved fix and reports change', () => {
    const crumbs = trail(2)
    expect(extendCrumbs(crumbs, 40 + 2 * STEP, -105, 2000, haversineMeters)).toBe(true)
    expect(crumbs.length).toBe(3)
  })

  it('ignores sub-threshold jitter (parked van)', () => {
    const crumbs = trail(2)
    const last = crumbs.at(-1)!
    expect(extendCrumbs(crumbs, last.lat + 1e-6, last.lon, 2000, haversineMeters)).toBe(false)
    expect(crumbs.length).toBe(2)
  })

  it('trims vertices older than the trail window', () => {
    const crumbs = trail(3)
    const now = CRUMB_MAX_AGE_MS + 1500 // makes crumbs at t=0 and t=1000 stale
    extendCrumbs(crumbs, 41, -105, now, haversineMeters)
    expect(crumbs[0].t).toBe(2000)
    expect(crumbs.at(-1)!.t).toBe(now)
  })

  it('halves the oldest tail at the count cap', () => {
    const crumbs = trail(CRUMB_MAX_COUNT, Date.now())
    const last = crumbs.at(-1)!
    extendCrumbs(crumbs, last.lat + STEP, last.lon, last.t + 1000, haversineMeters)
    expect(crumbs.length).toBeLessThanOrEqual(CRUMB_MAX_COUNT)
    // Newest point survives intact.
    expect(crumbs.at(-1)!.t).toBe(last.t + 1000)
  })
})
