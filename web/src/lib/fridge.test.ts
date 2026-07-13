import { describe, expect, it } from 'vitest'

import type { FridgeHistoryResponse } from './api'
import { FALLBACK_RANGE, clampSetpoint, formatTemp, historyToSeries } from './fridge'

const HIST: FridgeHistoryResponse = {
  span: 'hour',
  bucket_s: 3600,
  points: [
    { ts: '2026-07-13T15:00:00.000Z', dc_current_a: 1.1 },
    { ts: '2026-07-13T16:00:00.000Z', dc_current_a: null },
    { ts: '2026-07-13T17:00:00.000Z', dc_current_a: 0.3 },
  ],
  updated_at: '2026-07-13T17:05:00.000Z',
}

describe('historyToSeries', () => {
  it('maps buckets onto the Trend series shape', () => {
    const resp = historyToSeries(HIST)
    expect(resp.bucket_ms).toBe(3_600_000)
    expect(resp.x).toEqual(HIST.points.map((p) => Date.parse(p.ts)))
    expect(resp.series).toHaveLength(1)
    expect(resp.series[0].values).toEqual([1.1, null, 0.3])
    expect(resp.series[0].unit).toBe('A')
    expect(resp.start).toBe('2026-07-13T15:00:00.000Z')
    expect(resp.end).toBe('2026-07-13T17:00:00.000Z')
  })

  it('handles an empty span without erroring', () => {
    const resp = historyToSeries({ span: 'week', bucket_s: 604800, points: [], updated_at: null })
    expect(resp.x).toEqual([])
    expect(resp.series[0].values).toEqual([])
  })
})

describe('clampSetpoint', () => {
  it('clamps to the fridge-reported range and rounds to whole degrees', () => {
    const range = { min_c: -22, max_c: 10 }
    expect(clampSetpoint(4.4, range)).toBe(4)
    expect(clampSetpoint(99, range)).toBe(10)
    expect(clampSetpoint(-99, range)).toBe(-22)
  })

  it('falls back to the probed CFX3 range when ranges are unknown', () => {
    expect(clampSetpoint(-30, null)).toBe(FALLBACK_RANGE.min_c)
    expect(clampSetpoint(30, null)).toBe(FALLBACK_RANGE.max_c)
  })
})

describe('formatTemp', () => {
  it('renders the fridge presented unit (1 = °F), °C otherwise', () => {
    expect(formatTemp(-15, 1)).toBe('5°F')
    expect(formatTemp(-15, 0)).toBe('-15.0°C')
    expect(formatTemp(2.06, null)).toBe('2.1°C')
    expect(formatTemp(null, 1)).toBe('—')
  })
})
