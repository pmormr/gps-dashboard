/**
 * Drive HUD "records" derivation: today's high/low elevation and the
 * trailing sparkline, from the server's bucketed profile
 * (`/api/points/elevation`) plus the live fix. Pure — Drive.svelte feeds it
 * each fresh fix. The live altitude competes for the records immediately so
 * a new high registers on the dash the second it happens, not at the next
 * poll; the server's bucket mean then takes over once it lands.
 */

import type { ElevationBucket } from './api'

/** The sparkline's trailing window. */
export const SPARK_WINDOW_MS = 2 * 3600_000

/** One record: the altitude, when it was set, and whether the live fix set it. */
export interface Extreme {
  altitudeM: number
  timestamp: string
  live: boolean
}

export interface Records {
  high: Extreme | null
  low: Extreme | null
  /** Trailing-window altitudes (metres), oldest first, live value last. */
  spark: number[]
}

/**
 * Fold the bucketed profile and the live fix into the records.
 *
 * @param buckets   the server's mean buckets, oldest first
 * @param liveAltM  the raw fix's altitude, or null without a 3D fix
 * @param liveTs    the raw fix's timestamp (for a live-set record)
 * @param nowMs     the reference time for the sparkline window
 */
export function elevationRecords(
  buckets: readonly ElevationBucket[],
  liveAltM: number | null,
  liveTs: string | null,
  nowMs: number,
): Records {
  let high: Extreme | null = null
  let low: Extreme | null = null
  for (const b of buckets) {
    if (high == null || b.altitude > high.altitudeM) {
      high = { altitudeM: b.altitude, timestamp: b.timestamp, live: false }
    }
    if (low == null || b.altitude < low.altitudeM) {
      low = { altitudeM: b.altitude, timestamp: b.timestamp, live: false }
    }
  }
  if (liveAltM != null && Number.isFinite(liveAltM)) {
    const ts = liveTs ?? new Date(nowMs).toISOString()
    if (high == null || liveAltM > high.altitudeM) {
      high = { altitudeM: liveAltM, timestamp: ts, live: true }
    }
    if (low == null || liveAltM < low.altitudeM) {
      low = { altitudeM: liveAltM, timestamp: ts, live: true }
    }
  }

  const cutoff = nowMs - SPARK_WINDOW_MS
  const spark = buckets
    .filter((b) => Date.parse(b.timestamp) >= cutoff)
    .map((b) => b.altitude)
  if (liveAltM != null && Number.isFinite(liveAltM)) spark.push(liveAltM)
  return { high, low, spark: spark.length >= 2 ? spark : [] }
}
