/**
 * The shared track-window store — the `/api/points` fetch for the global
 * Selection window, decoupled from the map. One fetch per
 * window change feeds every consumer: the map trail, the TimeDock's density
 * lane, and window readouts — so Trends shows GPS density with no map on
 * screen. Consumers drive it: the mounted TimeDock calls `ensure(range)` from
 * an effect; identical windows dedup, so a view remount doesn't refetch.
 */

import { getPoints } from '../api'
import { errMsg } from '../errors'
import type { TrackPoint } from '../geo'
import type { Range } from './selection.svelte'

class TrackStore {
  points = $state<TrackPoint[]>([])
  truncated = $state(false)
  status = $state('')
  /** A window fetch is in flight (camera choreography waits on it — Map.svelte). */
  loading = $state(false)
  /** Human message when the loaded window has no points ('' otherwise). */
  empty = $state('')
  // The window the current points belong to (the strip domain). 0/0 before the
  // first load.
  loadedFromMs = $state(0)
  loadedToMs = $state(0)
  /**
   * Whether the map should refit to the new points — false when the fetch was a
   * live continuation (the anchor sliding forward).
   */
  refit = $state(true)

  /** The strip's hovered moment (ms), for the map's hover-scrub ghost dot. */
  hoverMs = $state<number | null>(null)

  private lastKey = ''
  private lastRange: Range | null = null
  private fetchToken = 0

  /** Fetch the window unless it's the one already loaded (or in flight). */
  async ensure(range: Range): Promise<void> {
    const key = `${range.from.getTime()}:${range.to.getTime()}`
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
    this.loading = true
    const token = ++this.fetchToken
    let data
    try {
      data = await getPoints(range.from.toISOString(), range.to.toISOString(), 20000)
    } catch (e) {
      if (token === this.fetchToken) {
        this.status = `Error: ${errMsg(e)}`
        this.lastKey = '' // allow a retry on the next ensure
        this.loading = false
      }
      return
    }
    if (token !== this.fetchToken) return // superseded by a newer window
    this.loading = false
    this.points = data.points
    this.truncated = data.truncated ?? false
    this.status = `${data.points.length.toLocaleString()} pts${data.truncated ? ' (truncated)' : ''}`
    this.empty = data.points.length ? '' : 'No GPS points for this window'
    this.loadedFromMs = range.from.getTime()
    this.loadedToMs = range.to.getTime()
    this.refit = !isLiveTick
  }
}

export const track = new TrackStore()
