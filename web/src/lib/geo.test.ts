import { describe, expect, it } from 'vitest'

import { haversineMeters, initialBearingDeg } from './geo'

describe('initialBearingDeg', () => {
  it('reports the cardinal directions from a mid-latitude origin', () => {
    expect(initialBearingDeg(39.0, -105.0, 40.0, -105.0)).toBeCloseTo(0, 5) // due north
    expect(initialBearingDeg(39.0, -105.0, 38.0, -105.0)).toBeCloseTo(180, 5) // due south
    // East/west along a parallel: the great circle starts slightly poleward of 90/270.
    expect(initialBearingDeg(39.0, -105.0, 39.0, -104.0)).toBeCloseTo(89.685, 2)
    expect(initialBearingDeg(39.0, -105.0, 39.0, -106.0)).toBeCloseTo(270.315, 2)
  })

  it('normalizes into [0, 360)', () => {
    const b = initialBearingDeg(39.0, -105.0, 38.5, -105.5)
    expect(b).toBeGreaterThanOrEqual(0)
    expect(b).toBeLessThan(360)
  })

  it('crosses the antimeridian the short way', () => {
    // From just west of the antimeridian to just east of it: eastbound (~90°),
    // not the 270° a naive lon difference would produce.
    const b = initialBearingDeg(0.0, 179.5, 0.0, -179.5)
    expect(b).toBeCloseTo(90, 1)
  })
})

describe('haversineMeters', () => {
  it('matches a known city-pair distance', () => {
    // Denver → Colorado Springs, ~101 km.
    const d = haversineMeters(39.7392, -104.9903, 38.8339, -104.8214)
    expect(d).toBeGreaterThan(99_000)
    expect(d).toBeLessThan(103_000)
  })
})
