/**
 * Live-position store — the Drive view's feed. Polls `/api/gpsd/live` at 1 Hz
 * while any consumer holds it started, and interpolates between the last two
 * fixes at rAF so the puck/camera glide instead of teleporting one second at a
 * time. Display therefore runs one poll interval behind real time — the
 * standard interpolation trade, invisible at road speeds.
 *
 * Heading is speed-gated at ingest (live.ts): below the gate the last good
 * bearing holds, so the camera doesn't spin at stoplights. Consumers read
 * `pos`/`heading` (smoothed) for the camera and `fix` (raw) for readouts; an
 * SSE upgrade later replaces the poll, not the consumers.
 */

import { getGpsdLive, type LiveFix } from '../api'
import { clamp01, gateHeading, lerpAngle, lerpPos } from '../live'

const POLL_MS = 1000
/** Fix age (s) beyond which a still-answering gpsd counts as stale. */
const STALE_S = 5

export type LiveStatus = 'connecting' | 'ok' | 'no-fix' | 'stale' | 'offline'

/** One ingested fix snapshot the rAF loop interpolates between. */
interface FixFrame {
  lat: number
  lon: number
  heading: number | null
  recvMs: number
}

class LiveStore {
  /** Latest raw poll payload (readouts: speed, alt, fix label…); null before the first. */
  fix = $state<LiveFix | null>(null)
  /** Interpolated display position, driven at rAF while started. */
  pos = $state<{ lat: number; lon: number } | null>(null)
  /** Gated + interpolated display heading, degrees true; null before any good reading. */
  heading = $state<number | null>(null)
  status = $state<LiveStatus>('connecting')

  private prev: FixFrame | null = null
  private cur: FixFrame | null = null
  private gated: number | null = null
  private consumers = 0
  private timer: ReturnType<typeof setTimeout> | null = null
  private raf = 0
  private pollToken = 0

  /** Begin polling (refcounted — the first consumer starts, the last `stop` halts). */
  start(): void {
    if (++this.consumers > 1) return
    this.status = 'connecting'
    void this.poll(++this.pollToken)
    this.raf = requestAnimationFrame(this.frame)
  }

  /** Release the store; polling and interpolation halt with the last consumer. */
  stop(): void {
    if (this.consumers === 0 || --this.consumers > 0) return
    this.pollToken++
    if (this.timer) clearTimeout(this.timer)
    this.timer = null
    cancelAnimationFrame(this.raf)
    this.prev = this.cur = null
  }

  private async poll(token: number): Promise<void> {
    try {
      const fix = await getGpsdLive()
      if (token !== this.pollToken) return
      this.ingest(fix)
    } catch {
      if (token !== this.pollToken) return
      this.status = 'offline'
    }
    this.timer = setTimeout(() => void this.poll(token), POLL_MS)
  }

  private ingest(fix: LiveFix): void {
    this.fix = fix
    if (!fix.connected) {
      this.status = 'offline'
      return
    }
    if (fix.mode < 2 || fix.lat == null || fix.lon == null) {
      this.status = 'no-fix'
      return
    }
    this.status = fix.fix_age_s != null && fix.fix_age_s > STALE_S ? 'stale' : 'ok'
    this.gated = gateHeading(this.gated, fix.track, fix.speed)
    this.prev = this.cur
    this.cur = { lat: fix.lat, lon: fix.lon, heading: this.gated, recvMs: performance.now() }
  }

  private frame = (): void => {
    const { prev, cur } = this
    if (cur) {
      if (prev) {
        const t = clamp01((performance.now() - cur.recvMs) / POLL_MS)
        this.pos = lerpPos(prev, cur, t)
        this.heading =
          prev.heading != null && cur.heading != null
            ? lerpAngle(prev.heading, cur.heading, t)
            : cur.heading
      } else {
        this.pos = { lat: cur.lat, lon: cur.lon }
        this.heading = cur.heading
      }
    }
    this.raf = requestAnimationFrame(this.frame)
  }
}

export const live = new LiveStore()
