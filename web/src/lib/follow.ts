/**
 * Follow-camera policy for the Drive view: course-up, pitched, speed-scaled
 * zoom. Pure helpers only — the loop that applies poses lives in Drive.svelte,
 * reacting to the live store's rAF-interpolated position. Every constant here
 * is a road-tuning knob (drive-view plan, open decision B).
 */

/** Camera pitch while following, degrees. */
export const FOLLOW_PITCH_DEG = 50

/** Zoom at/below SPEED_LO_MS (crawling) and at/above SPEED_HI_MS (highway). */
export const ZOOM_MAX = 17
export const ZOOM_MIN = 13
export const SPEED_LO_MS = 3 // ~7 mph
export const SPEED_HI_MS = 29 // ~65 mph

/** Max zoom change per second — keeps speed noise from pumping the camera. */
export const ZOOM_SLEW_PER_S = 0.6

/** Duration of the ease into the follow pose on view enter, ms. */
export const ENTER_EASE_MS = 800

/** Vector-label text-size multiplier while Drive owns the map (dash readability). */
export const LABEL_SCALE = 1.4

/** Target zoom for a ground speed (m/s): linear between the curve endpoints. */
export function speedZoom(speedMs: number | null): number {
  if (speedMs == null || speedMs <= SPEED_LO_MS) return ZOOM_MAX
  if (speedMs >= SPEED_HI_MS) return ZOOM_MIN
  const t = (speedMs - SPEED_LO_MS) / (SPEED_HI_MS - SPEED_LO_MS)
  return ZOOM_MAX + (ZOOM_MIN - ZOOM_MAX) * t
}

/** Move `current` toward `target` by at most `maxDelta` (rate-limited zoom). */
export function slew(current: number, target: number, maxDelta: number): number {
  const d = target - current
  if (d > maxDelta) return current + maxDelta
  if (d < -maxDelta) return current - maxDelta
  return target
}
