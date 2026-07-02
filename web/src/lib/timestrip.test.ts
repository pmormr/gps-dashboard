/**
 * TimeStrip geometry tests — the pure wheel-zoom math (time-dock Phase 2). The
 * canvas widget itself is exercised manually; `zoomDomain` carries the logic
 * worth pinning: anchor invariance, direction, and the span clamps.
 */

import { describe, expect, it } from 'vitest'

import { zoomDomain } from './timestrip'

const HOUR = 3600_000
const DAY = 24 * HOUR
const YEAR = 365 * DAY

describe('zoomDomain', () => {
  const d = { startMs: 0, endMs: 10 * HOUR }

  it('holds the anchor at the same fractional position', () => {
    const anchor = 2.5 * HOUR // 25% into the domain
    const z = zoomDomain(d, anchor, 0.5)
    expect(z.endMs - z.startMs).toBe(5 * HOUR)
    expect((anchor - z.startMs) / (z.endMs - z.startMs)).toBeCloseTo(0.25)
  })

  it('factor > 1 widens, factor < 1 narrows', () => {
    const anchor = 5 * HOUR
    expect(zoomDomain(d, anchor, 2).endMs - zoomDomain(d, anchor, 2).startMs).toBe(20 * HOUR)
    expect(zoomDomain(d, anchor, 0.5).endMs - zoomDomain(d, anchor, 0.5).startMs).toBe(5 * HOUR)
  })

  it('clamps the span to the max (a year by default)', () => {
    const wide = { startMs: 0, endMs: 300 * DAY }
    const z = zoomDomain(wide, 150 * DAY, 10)
    expect(z.endMs - z.startMs).toBe(YEAR)
  })

  it('clamps the span to the min (a minute by default)', () => {
    const narrow = { startMs: 0, endMs: 120_000 }
    const z = zoomDomain(narrow, 60_000, 0.01)
    expect(z.endMs - z.startMs).toBe(60_000)
    // Anchor at the midpoint stays the midpoint even when the clamp kicks in.
    expect((60_000 - z.startMs) / (z.endMs - z.startMs)).toBeCloseTo(0.5)
  })

  it('centers on a degenerate zero-span domain instead of dividing by zero', () => {
    const z = zoomDomain({ startMs: 5 * HOUR, endMs: 5 * HOUR }, 5 * HOUR, 2, HOUR, YEAR)
    expect(z.endMs - z.startMs).toBe(HOUR)
    expect((z.startMs + z.endMs) / 2).toBe(5 * HOUR)
  })
})
