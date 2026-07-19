import { describe, expect, it } from 'vitest'

import { fmtDurationSecs, haversineMeters, initialBearingDeg } from './geo'

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

describe('fmtDurationSecs', () => {
  it('renders null/undefined as an em dash', () => {
    expect(fmtDurationSecs(null)).toBe('—')
    expect(fmtDurationSecs(undefined)).toBe('—')
  })

  it('defaults to floored hours/minutes (the geo/popup variant)', () => {
    expect(fmtDurationSecs(0)).toBe('0m')
    expect(fmtDurationSecs(59)).toBe('0m') // sub-minute floors away
    expect(fmtDurationSecs(90)).toBe('1m')
    expect(fmtDurationSecs(3661)).toBe('1h 1m')
    expect(fmtDurationSecs(90000)).toBe('25h 0m') // no days → hours accumulate
  })

  it('with days shows the top d/h pair (Home.dur)', () => {
    expect(fmtDurationSecs(90000, { days: true })).toBe('1d 1h')
    expect(fmtDurationSecs(3661, { days: true })).toBe('1h 1m')
    expect(fmtDurationSecs(30, { days: true })).toBe('0m')
  })

  it('with showSecs drops to a seconds floor (InspectPanel.fmtSecs)', () => {
    expect(fmtDurationSecs(0, { showSecs: true })).toBe('0s')
    expect(fmtDurationSecs(45, { showSecs: true })).toBe('45s')
    expect(fmtDurationSecs(90, { showSecs: true })).toBe('1m 30s')
    expect(fmtDurationSecs(3661, { showSecs: true })).toBe('1h 1m') // secs hidden once hours show
  })

  it('with padMin zero-pads minutes beside hours (Sky.humanGap)', () => {
    expect(fmtDurationSecs(300, { padMin: true })).toBe('5m') // bare minutes unpadded
    expect(fmtDurationSecs(3600, { padMin: true })).toBe('1h 00m')
    expect(fmtDurationSecs(3660, { padMin: true })).toBe('1h 01m')
  })
})
