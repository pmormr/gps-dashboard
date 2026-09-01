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

/** One playback-window preset — how far back the scrubber reaches. */
export interface WindowPreset {
  hours: number
  label: string
  title: string
}

/**
 * Playback-window presets. Every window scrubs at native capture granularity
 * (~7 min frames): the scrubber indexes the full frame list, and only a
 * sliding neighborhood around the playhead is loaded as MapLibre sources
 * (LOADED_FRAME_CAP). 24h matches the archive retention (weather/registry.py).
 */
export const WINDOW_PRESETS: readonly WindowPreset[] = [
  { hours: 6, label: '6h', title: 'Scrub the last 6 hours of radar' },
  { hours: 24, label: '24h', title: 'Scrub the whole 24-hour archive' },
]

/**
 * How many frames around the playhead stay loaded as raster sources/layers.
 * Each loaded frame preloads its viewport tiles while the camera rests (the
 * warm mechanism), so the cap bounds layer count, background fetch, and GPU
 * tile textures — ~50 is the proven-comfortable load of the old full-window
 * design.
 */
export const LOADED_FRAME_CAP = 48

/** Recenter the loaded neighborhood when the playhead gets this close to its edge. */
export const RECENTER_MARGIN = 8

/**
 * How many frames ahead of the playhead get primed (made visible so their
 * tiles fetch) during loop playback. Bounded so the link serves the near
 * future first — a whole-deck warm queues hundreds of tiles in front of the
 * frame the loop needs next; frames behind the playhead stay visible, so by
 * the second loop the whole window is resident anyway.
 */
export const PLAYBACK_LOOKAHEAD = 4

/** An index range [start, end) into the frame list — the loaded neighborhood. */
export interface FrameRange {
  start: number
  end: number
}

/**
 * The loaded-neighborhood range for a playhead position: `cap` indices
 * centered on `index`, clamped to the list — the whole list when it fits.
 */
export function sliceRange(total: number, index: number, cap: number): FrameRange {
  if (total <= cap) return { start: 0, end: total }
  let start = index - Math.floor(cap / 2)
  if (start < 0) start = 0
  if (start + cap > total) start = total - cap
  return { start, end: start + cap }
}

/**
 * Whether the loaded neighborhood should recenter on the playhead: the index
 * left the range, or came within `margin` of an edge that has frames beyond it.
 */
export function needsRecenter(
  range: FrameRange,
  total: number,
  index: number,
  margin: number,
): boolean {
  if (index < range.start || index >= range.end) return true
  if (index - range.start < margin && range.start > 0) return true
  if (range.end - 1 - index < margin && range.end < total) return true
  return false
}

/**
 * Fetch a preset's frame index, ascending (old → new) — the order the
 * scrubber and loop advance through. Fetch only: the view owns which slice of
 * the index is loaded as sources (the sliding neighborhood).
 *
 * @param preset The window preset (reach).
 * @returns The available frame instants (epoch-ms), ascending.
 */
export async function loadRadar(preset: WindowPreset): Promise<number[]> {
  const resp = await getWeatherFrames(RADAR_LAYER, preset.hours)
  return [...resp.frames].sort((a, b) => a - b)
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
