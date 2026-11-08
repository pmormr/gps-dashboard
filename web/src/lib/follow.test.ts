import { describe, expect, it } from 'vitest'

import {
  mphToMs,
  slew,
  SPEED_HI_MS,
  SPEED_LO_MS,
  speedZoom,
  ZOOM_MAX,
  ZOOM_MIN,
} from './follow'

describe('speedZoom', () => {
  it('pins to max zoom when crawling or speed unknown', () => {
    expect(speedZoom(null)).toBe(ZOOM_MAX)
    expect(speedZoom(0)).toBe(ZOOM_MAX)
    expect(speedZoom(SPEED_LO_MS)).toBe(ZOOM_MAX)
  })

  it('pins to min zoom at highway speed', () => {
    expect(speedZoom(SPEED_HI_MS)).toBe(ZOOM_MIN)
    expect(speedZoom(40)).toBe(ZOOM_MIN)
  })

  it('interpolates linearly in between', () => {
    const mid = (SPEED_LO_MS + SPEED_HI_MS) / 2
    expect(speedZoom(mid)).toBeCloseTo((ZOOM_MAX + ZOOM_MIN) / 2)
  })

  it('is monotonically non-increasing with speed', () => {
    let prev = speedZoom(0)
    for (let v = 1; v <= 45; v++) {
      const z = speedZoom(v)
      expect(z).toBeLessThanOrEqual(prev)
      prev = z
    }
  })

  it('takes the highway zoom and the full-zoom-out speed as settings', () => {
    // Further out at speed, reached sooner: 30 mph already sits at the floor.
    const hi = mphToMs(30)
    expect(speedZoom(hi, 11, hi)).toBe(11)
    expect(speedZoom(mphToMs(45), 11, hi)).toBe(11)
    expect(speedZoom((SPEED_LO_MS + hi) / 2, 11, hi)).toBeCloseTo((ZOOM_MAX + 11) / 2)
    expect(speedZoom(0, 11, hi)).toBe(ZOOM_MAX)
  })

  it('degenerates to a step, not NaN, when the full-zoom-out speed is at the crawl threshold', () => {
    expect(speedZoom(SPEED_LO_MS, 12, SPEED_LO_MS)).toBe(ZOOM_MAX)
    expect(speedZoom(SPEED_LO_MS + 0.01, 12, SPEED_LO_MS)).toBe(12)
  })
})

describe('mphToMs', () => {
  it('converts statute mph', () => {
    expect(mphToMs(65)).toBeCloseTo(29.06, 2)
  })
})

describe('slew', () => {
  it('caps the step in both directions', () => {
    expect(slew(14, 16, 0.5)).toBe(14.5)
    expect(slew(14, 12, 0.5)).toBe(13.5)
  })

  it('lands exactly when within the cap', () => {
    expect(slew(14, 14.2, 0.5)).toBe(14.2)
    expect(slew(14, 14, 0.5)).toBe(14)
  })
})
