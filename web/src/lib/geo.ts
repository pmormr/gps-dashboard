/**
 * Pure geo + formatting helpers for the map view.
 *
 * No DOM, no MapLibre — just distance/stat math and unit formatting shared by the
 * map engine (`map.ts`), the timeline, and the inspect-window panel.
 */

/** A point from `/api/points` (the processed `track_points` tier). */
export interface TrackPoint {
  timestamp: string
  lat: number
  lon: number
  speed?: number | null
  altitude?: number | null
  kind?: string
  n_raw?: number
  importance?: number
  accuracy?: number
  dwell_start?: string | null
  dwell_end?: string | null
  radius?: number
}

/** Window stats over a set of track points. */
export interface TrackStats {
  distance: number
  maxSpeed: number | null
  avgSpeed: number | null
  elevRange: number | null
  durationMs: number
  pointCount: number
}

/** Great-circle distance between two lat/lon points, in metres. */
export function haversineMeters(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371000
  const f1 = (lat1 * Math.PI) / 180
  const f2 = (lat2 * Math.PI) / 180
  const df = ((lat2 - lat1) * Math.PI) / 180
  const dl = ((lon2 - lon1) * Math.PI) / 180
  const a = Math.sin(df / 2) ** 2 + Math.cos(f1) * Math.cos(f2) * Math.sin(dl / 2) ** 2
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

/** Initial great-circle bearing from point 1 toward point 2, degrees [0, 360). */
export function initialBearingDeg(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const f1 = (lat1 * Math.PI) / 180
  const f2 = (lat2 * Math.PI) / 180
  const dl = ((lon2 - lon1) * Math.PI) / 180
  const y = Math.sin(dl) * Math.cos(f2)
  const x = Math.cos(f1) * Math.sin(f2) - Math.sin(f1) * Math.cos(f2) * Math.cos(dl)
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360
}

// gpsd reports Doppler-derived ground speed, which is reliable even at low speed
// unlike differencing the noisy position. Below this, the receiver is parked and
// its position wander is GPS jitter, not travel — so those segments are excluded
// from distance to stop a parked stop from inflating the total.
const SPEED_GATE_MPS = 0.5

/** Distance / speed / elevation-range / duration over an ordered track. */
export function computeStats(points: TrackPoint[]): TrackStats | null {
  if (!points.length) return null

  let distance = 0
  let elevMin: number | null = null
  let elevMax: number | null = null
  const speeds: number[] = []

  for (let i = 0; i < points.length; i++) {
    const alt = points[i].altitude
    if (alt != null) {
      if (elevMin == null || alt < elevMin) elevMin = alt
      if (elevMax == null || alt > elevMax) elevMax = alt
    }
    // Null speed (schema permits it) counts as moving so we never drop real travel.
    const sp = points[i].speed
    if (i > 0 && (sp == null || sp >= SPEED_GATE_MPS)) {
      distance += haversineMeters(points[i - 1].lat, points[i - 1].lon, points[i].lat, points[i].lon)
    }
    if (sp != null) speeds.push(sp)
  }

  const maxSpeed = speeds.length ? Math.max(...speeds) : null
  const avgSpeed = speeds.length ? speeds.reduce((a, b) => a + b, 0) / speeds.length : null
  // GPS altitude is too noisy to sum into a trustworthy cumulative gain (it scales
  // with sample count, not terrain), so report the honest min-to-max range instead.
  const elevRange = elevMin != null && elevMax != null ? elevMax - elevMin : null
  const durationMs =
    new Date(points.at(-1)!.timestamp).getTime() - new Date(points[0].timestamp).getTime()

  return { distance, maxSpeed, avgSpeed, elevRange, durationMs, pointCount: points.length }
}

/** Format metres as miles. */
export function fmtDistance(meters: number): string {
  const miles = meters / 1609.344
  return `${miles >= 10 ? miles.toFixed(1) : miles.toFixed(2)} mi`
}

/** Format m/s as mph (— when null). */
export function fmtSpeed(mps: number | null): string {
  return mps != null ? `${(mps * 2.23694).toFixed(1)} mph` : '—'
}

/** Format metres as feet (— when null). */
export function fmtAltitude(meters: number | null): string {
  return meters != null ? `${Math.round(meters * 3.28084)} ft` : '—'
}

/** Format a duration in ms as `Hh Mm` / `Mm`. */
export function fmtDuration(ms: number): string {
  const h = Math.floor(ms / 3600000)
  const m = Math.floor((ms % 3600000) / 60000)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

/** Local HH:MM for an ISO/canonical timestamp. */
export function fmtTime(isoString: string): string {
  return new Date(isoString).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

/** Local short date for an ISO/canonical timestamp. */
export function fmtDate(isoString: string): string {
  return new Date(isoString).toLocaleDateString([], {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}
