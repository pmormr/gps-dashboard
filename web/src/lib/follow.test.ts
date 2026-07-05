import { describe, expect, it } from 'vitest'

import {
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
