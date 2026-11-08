/**
 * Follow-camera policy for the Drive view: course-up, pitched, speed-scaled
 * zoom. Pure helpers only — the loop that applies poses lives in Drive.svelte,
 * reacting to the live store's rAF-interpolated position. Every constant here
 * is a road-tuning knob (drive-view plan, open decision B).
 */

/** Camera pitch while following, degrees. */
export const FOLLOW_PITCH_DEG = 50

/** Zoom at/below SPEED_LO_MS (crawling) and at/above the highway speed. ZOOM_MIN
 *  (how far out at full speed) and SPEED_HI_MS (where full zoom-out is reached)
 *  are the defaults of two user settings (`drivePrefs.ts` zoomMin / zoomHiMph). */
export const ZOOM_MAX = 17
export const ZOOM_MIN = 13
export const SPEED_LO_MS = 3 // ~7 mph
export const SPEED_HI_MS = 29 // ~65 mph

/** Max zoom change per second — keeps speed noise from pumping the camera; the
 *  default of the `zoomSlew` user setting (the zoom "ramp"). */
export const ZOOM_SLEW_PER_S = 0.6

const MPH_PER_MS = 2.236936

/** Statute mph → m/s (the settings are in mph, the speed curve in m/s). */
export function mphToMs(mph: number): number {
  return mph / MPH_PER_MS
}

/** Duration of the ease into the follow pose on view enter, ms. */
export const ENTER_EASE_MS = 800

/** Vector-label text-size multiplier while Drive owns the map (dash readability). */
export const LABEL_SCALE = 1.4

/**
 * Target zoom for a ground speed (m/s): linear from ZOOM_MAX at SPEED_LO_MS
 * down to `zoomMin` at `speedHiMs`. A `speedHiMs` at or below SPEED_LO_MS
 * degenerates to a step at SPEED_LO_MS rather than dividing by zero.
 */
export function speedZoom(
  speedMs: number | null,
  zoomMin = ZOOM_MIN,
  speedHiMs = SPEED_HI_MS,
): number {
  if (speedMs == null || speedMs <= SPEED_LO_MS) return ZOOM_MAX
  if (speedMs >= speedHiMs) return zoomMin
  const t = (speedMs - SPEED_LO_MS) / (speedHiMs - SPEED_LO_MS)
  return ZOOM_MAX + (zoomMin - ZOOM_MAX) * t
}

/** Move `current` toward `target` by at most `maxDelta` (rate-limited zoom). */
export function slew(current: number, target: number, maxDelta: number): number {
  const d = target - current
  if (d > maxDelta) return current + maxDelta
  if (d < -maxDelta) return current - maxDelta
  return target
}
