/**
 * Pure math for the live-position feed: the heading gate and the interpolation
 * the store runs at rAF between 1 Hz fixes. Kept free of Svelte/DOM so it's
 * unit-testable (live.test.ts); the polling/state lives in stores/live.svelte.ts.
 */

/**
 * Speed (m/s) below which gpsd `track` is Doppler noise and must not steer the
 * camera — the v1 answer to the stoplight-spin trap (an IMU compass is the
 * eventual parked-heading fix, plans/motion-imu-plan.md).
 */
export const HEADING_GATE_MS = 1.0

/**
 * Gate a new course-over-ground reading against the speed it was measured at.
 *
 * Returns the new track when it's trustworthy (speed at/above the gate),
 * otherwise holds the previous heading. Null only before any good reading.
 */
export function gateHeading(
  prev: number | null,
  track: number | null,
  speed: number | null,
): number | null {
  if (track == null || speed == null || speed < HEADING_GATE_MS) return prev
  return track
}

/** Shortest signed angular difference `to − from`, in (−180, 180]. */
export function angleDelta(from: number, to: number): number {
  let d = (to - from) % 360
  if (d > 180) d -= 360
  if (d <= -180) d += 360
  return d
}

/** Interpolate between two bearings along the shortest arc; result in [0, 360). */
export function lerpAngle(from: number, to: number, t: number): number {
  const a = from + angleDelta(from, to) * t
  return ((a % 360) + 360) % 360
}

/** Linear position interpolation — fine at 1 s fix spacing and road speeds. */
export function lerpPos(
  a: { lat: number; lon: number },
  b: { lat: number; lon: number },
  t: number,
): { lat: number; lon: number } {
  return { lat: a.lat + (b.lat - a.lat) * t, lon: a.lon + (b.lon - a.lon) * t }
}

/** Clamp an interpolation fraction to [0, 1] (never extrapolate past the fix). */
export function clamp01(t: number): number {
  return t < 0 ? 0 : t > 1 ? 1 : t
}

const WINDS = [
  'N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW',
]

/** 16-wind compass point for a bearing in degrees true. */
export function cardinal(deg: number): string {
  return WINDS[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16]
}

/** One Drive-breadcrumb vertex (client-side trail state). */
export interface Crumb {
  lat: number
  lon: number
  /** Fix time, epoch ms. */
  t: number
}

/** Minimum movement before a live fix extends the breadcrumb, metres. */
export const CRUMB_MIN_METERS = 5
/** Trail length kept client-side (matches the seed window), ms. */
export const CRUMB_MAX_AGE_MS = 30 * 60_000
/** Hard cap on client-held vertices (a 30 min drive at 1 Hz would be 1800). */
export const CRUMB_MAX_COUNT = 1200

/**
 * Extend the breadcrumb with a live fix, in place: append only when the van
 * moved ≥ CRUMB_MIN_METERS (a parked van must not grow a fuzzball), then trim
 * vertices older than the trail window and enforce the count cap (dropping
 * every other oldest vertex — the far tail loses fidelity first).
 *
 * Returns true when the trail changed (the caller re-renders only then).
 */
export function extendCrumbs(
  crumbs: Crumb[],
  lat: number,
  lon: number,
  t: number,
  distMeters: (aLat: number, aLon: number, bLat: number, bLon: number) => number,
): boolean {
  const last = crumbs.at(-1)
  if (last && distMeters(last.lat, last.lon, lat, lon) < CRUMB_MIN_METERS) return false
  crumbs.push({ lat, lon, t })
  const cutoff = t - CRUMB_MAX_AGE_MS
  let drop = 0
  while (drop < crumbs.length - 1 && crumbs[drop].t < cutoff) drop++
  if (drop > 0) crumbs.splice(0, drop)
  if (crumbs.length > CRUMB_MAX_COUNT) {
    const spare = crumbs.splice(0, Math.floor(crumbs.length / 2))
    crumbs.unshift(...spare.filter((_, i) => i % 2 === 0))
  }
  return true
}
