/**
 * Weather-radar overlay controller + pure frame helpers.
 *
 * The animation half of the radar tier (plans/weather-plan.md): fetches the
 * recent frame window from /api/weather/radar/frames, loads each frame's
 * per-frame PMTiles as a raster source through the MapView façade, and hands the
 * Weather view an ascending frame list it scrubs/animates over (visibility
 * toggle). Imports no MapLibre — all map work goes through the façade — so this
 * stays a thin, testable seam and the chrome can import the labels without
 * pulling the map engine into the main bundle.
 */

import type { FeatureCollection } from 'geojson'

import { getWeatherFrames, getWeatherGeojson } from './api'
import type { MapView as MapViewType } from './map'

type View = typeof MapViewType

/** The radar layer id (the registry's raster entry one; the frontend seam for more). */
export const RADAR_LAYER = 'radar'

/** Playback-window presets (hours) — how far back the loaded loop reaches. */
export const WINDOW_PRESETS = [1, 3, 6] as const

/**
 * Fetch the recent frame window, load the frames as raster sources, and return
 * them ascending (old → new) — the order the scrubber and loop advance through.
 *
 * @param view The MapView façade.
 * @param windowHours How far back to load (a WINDOW_PRESETS value).
 * @returns The loaded frame instants (epoch-ms), ascending.
 */
export async function loadRadar(view: View, windowHours: number): Promise<number[]> {
  const resp = await getWeatherFrames(RADAR_LAYER, windowHours)
  const frames = [...resp.frames].sort((a, b) => a - b)
  view.setRadarFrames(frames)
  return frames
}

/** Tear down the radar overlay (view-leave). */
export function clearRadar(view: View): void {
  view.clearRadar()
}

/** The result of a warnings load: the rendered set + its snapshot age. */
export interface WarningsResult {
  count: number
  fetchedAt: string | null
}

/**
 * Fetch the warnings snapshot, drop expired features, and render the rest.
 *
 * NWS zone-only alerts carry null geometry (they don't render) — the count is
 * of features that survive the expiry filter, geometry or not.
 *
 * @param view The MapView façade.
 * @param nowMs Current time (injected for testability) — features whose
 *   `expires` is at or before this are dropped.
 * @returns The rendered feature count and the snapshot's fetch time.
 */
export async function loadWarnings(view: View, nowMs: number): Promise<WarningsResult> {
  const data = await getWeatherGeojson('warnings')
  const features = (data.features ?? []).filter((f) => {
    const expires = f.properties?.expires
    return !expires || Date.parse(String(expires)) > nowMs
  })
  const fc: FeatureCollection = { type: 'FeatureCollection', features }
  view.setWarnings(fc)
  return { count: features.length, fetchedAt: data.fetched_at }
}

/** Clear the warnings overlay. */
export function clearWarnings(view: View): void {
  view.clearWarnings()
}

/** Format a frame instant (epoch-ms) as a local HH:MM label (the playhead clock). */
export function frameTimeLabel(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

/** Format a frame instant as a local date + time (older frames / archive extent). */
export function frameDateLabel(ms: number): string {
  return new Date(ms).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * A compact "how long ago" label for a frame instant, given now.
 *
 * Pure (now injected) so it's deterministic to test. Sub-minute reads "now",
 * then minutes, then hours+minutes.
 */
export function frameAgeLabel(ms: number, nowMs: number): string {
  const sec = Math.max(0, Math.round((nowMs - ms) / 1000))
  if (sec < 60) return 'now'
  const min = Math.round(sec / 60)
  if (min < 60) return `${min} min ago`
  const h = Math.floor(min / 60)
  const m = min % 60
  return m ? `${h}h ${m}m ago` : `${h}h ago`
}
