/**
 * The shared track-window store — the `/api/points` fetch for the global
 * Selection window, decoupled from the map (time-dock Phase 3). One fetch per
 * window change feeds every consumer: the map trail, the TimeDock's density
 * lane, and window readouts — so Trends shows GPS density with no map on
 * screen. Consumers drive it: the mounted TimeDock calls `ensure(range)` from
 * an effect; identical windows dedup, so a view remount doesn't refetch.
 */

import { getPoints } from '../api'
import type { TrackPoint } from '../geo'
import type { Range } from './selection.svelte'

class TrackStore {
  points = $state<TrackPoint[]>([])
  truncated = $state(false)
  status = $state('')
  /** Human message when the loaded window has no points ('' otherwise). */
  empty = $state('')
  // The window the current points belong to (the strip domain). 0/0 before the
  // first load.
  loadedFromMs = $state(0)
  loadedToMs = $state(0)
  /**
   * Whether the map should refit to the new points — false when the fetch was a
   * live continuation (the anchor sliding forward) or a bbox-filtered fetch
   * (refitting to in-view points would move the viewport → new bbox → loop).
   */
  refit = $state(true)

  /**
   * Viewport filter (time-dock Phase 5): when set (W,S,E,N), fetches pass it as
   * `bbox` so the density lane shows only time spent in view. Map.svelte owns it
   * (updates on moveend while the toggle is on; null on toggle-off/unmount).
   */
  bbox = $state<string | null>(null)

  /** The strip's hovered moment (ms), for the map's hover-scrub ghost dot. */
  hoverMs = $state<number | null>(null)

  private lastKey = ''
  private lastRange: Range | null = null
  private fetchToken = 0

  /** Fetch the window unless it's the one already loaded (or in flight). */
  async ensure(range: Range): Promise<void> {
    const bbox = this.bbox
    const key = `${range.from.getTime()}:${range.to.getTime()}:${bbox ?? ''}`
    if (key === this.lastKey) return
    this.lastKey = key
    const isLiveTick =
      !!this.lastRange &&
      this.lastRange.live &&
      range.live &&
      this.lastRange.mode === range.mode &&
      this.lastRange.windowMs === range.windowMs
    this.lastRange = range
    this.status = 'Loading…'
    const token = ++this.fetchToken
    let data
    try {
      data = await getPoints(range.from.toISOString(), range.to.toISOString(), 20000, bbox)
    } catch (e) {
      if (token === this.fetchToken) {
        this.status = `Error: ${e instanceof Error ? e.message : String(e)}`
        this.lastKey = '' // allow a retry on the next ensure
      }
      return
    }
    if (token !== this.fetchToken) return // superseded by a newer window
    this.points = data.points
    this.truncated = data.truncated ?? false
    this.status = `${data.points.length.toLocaleString()} pts${data.truncated ? ' (truncated)' : ''}`
    this.empty = data.points.length ? '' : 'No GPS points for this window'
    this.loadedFromMs = range.from.getTime()
    this.loadedToMs = range.to.getTime()
    this.refit = !isLiveTick && bbox == null
  }
}

export const track = new TrackStore()
