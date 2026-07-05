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
