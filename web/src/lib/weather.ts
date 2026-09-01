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

/** One playback-window preset — how far back the loaded loop/scrub reaches. */
export interface WindowPreset {
  hours: number
  label: string
  title: string
  /** Frame budget: bigger windows thin evenly by time to stay under it. */
  maxFrames: number
}

/**
 * Playback-window presets. Every loaded frame is a MapLibre raster source +
 * layer whose viewport tiles fully preload while the camera rests (the warm
 * mechanism), so the budget bounds layer count, background fetch, and GPU tile
 * textures — the 2-week archive (~2,900 frames) can't load 1:1. 6 h is the
 * full-fidelity live window; 24 h ≈ 20-min steps; 2 w ≈ 4-h steps. Full-
 * fidelity lazy scrubbing of the archive stays the documented follow-up
 * (plans/weather-plan.md).
 */
export const WINDOW_PRESETS: readonly WindowPreset[] = [
  { hours: 6, label: '6h', title: 'Load the last 6 hours of radar', maxFrames: 90 },
  {
    hours: 24,
    label: '24h',
    title: 'Load the last 24 hours of radar (thinned to ~20 min steps)',
    maxFrames: 72,
  },
  {
    hours: 336,
    label: '2w',
    title: 'Load the whole 2-week archive (thinned to ~4 h steps)',
    maxFrames: 84,
  },
]

/**
 * Thin a frame list to at most `maxFrames`, evenly spaced by time.
 *
 * Picks the closest on-disk frame to each evenly spaced instant across the
 * span (newest and oldest always survive), so archive gaps don't skew the
 * spread and duplicates collapse. Ascending in, ascending out.
 */
export function decimateFrames(frames: number[], maxFrames: number): number[] {
  if (frames.length <= maxFrames) return frames.slice()
  const newest = frames[frames.length - 1]
  const oldest = frames[0]
  const step = (newest - oldest) / (maxFrames - 1)
  const picked = new Set<number>()
  for (let i = 0; i < maxFrames; i++) picked.add(closestFrame(frames, newest - i * step))
  return [...picked].sort((a, b) => a - b)
}

/** Binary-search the frame closest to a target instant (frames ascending). */
function closestFrame(frames: number[], target: number): number {
  let lo = 0
  let hi = frames.length - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (frames[mid] < target) lo = mid + 1
    else hi = mid
  }
  if (lo > 0 && target - frames[lo - 1] < frames[lo] - target) return frames[lo - 1]
  return frames[lo]
}

/**
 * Fetch a preset's frame window, thin it to the preset's budget, load the
 * survivors as raster sources, and return them ascending (old → new) — the
 * order the scrubber and loop advance through.
 *
 * @param view The MapView façade.
 * @param preset The window preset (reach + frame budget).
 * @returns The loaded frame instants (epoch-ms), ascending.
 */
export async function loadRadar(view: View, preset: WindowPreset): Promise<number[]> {
  const resp = await getWeatherFrames(RADAR_LAYER, preset.hours)
  const frames = decimateFrames(
    [...resp.frames].sort((a, b) => a - b),
    preset.maxFrames,
  )
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
 * Compact byte-count label for the tile-load readout (B → kB → MB).
 *
 * Binary units; MB keeps one decimal below 10 MB, whole numbers above — the
 * readout stays short while it climbs during a session.
 */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1_048_576) return `${Math.round(n / 1024)} kB`
  const mb = n / 1_048_576
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`
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
