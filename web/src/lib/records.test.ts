import { describe, expect, it } from 'vitest'

import type { ElevationBucket } from './api'
import { elevationRecords, SPARK_WINDOW_MS } from './records'

const NOW = Date.parse('2026-11-07T18:00:00.000Z')
const minutesAgo = (m: number): string => new Date(NOW - m * 60_000).toISOString()
const bucket = (m: number, altitude: number): ElevationBucket => ({
  timestamp: minutesAgo(m),
  altitude,
  n: 1,
})

describe('elevationRecords', () => {
  it('takes the extremes from the buckets with their times', () => {
    const r = elevationRecords([bucket(300, 2000), bucket(200, 3500), bucket(100, 1500)], null, null, NOW)
    expect(r.high).toEqual({ altitudeM: 3500, timestamp: minutesAgo(200), live: false })
    expect(r.low).toEqual({ altitudeM: 1500, timestamp: minutesAgo(100), live: false })
  })

  it('lets the live fix set a record immediately', () => {
    const r = elevationRecords([bucket(60, 2000), bucket(30, 2100)], 2500, minutesAgo(0), NOW)
    expect(r.high).toEqual({ altitudeM: 2500, timestamp: minutesAgo(0), live: true })
    expect(r.low?.live).toBe(false)
  })

  it('is empty without data, and a lone live fix is a record but no sparkline', () => {
    expect(elevationRecords([], null, null, NOW)).toEqual({ high: null, low: null, spark: [] })
    const r = elevationRecords([], 1800, null, NOW)
    expect(r.high?.altitudeM).toBe(1800)
    expect(r.spark).toEqual([])
  })

  it('sparklines only the trailing window, live value last', () => {
    const old = bucket(SPARK_WINDOW_MS / 60_000 + 1, 9000)
    const r = elevationRecords([old, bucket(90, 2000), bucket(45, 2200)], 2300, null, NOW)
    expect(r.spark).toEqual([2000, 2200, 2300])
    expect(r.high?.altitudeM).toBe(9000) // records still span the whole day
  })
})
