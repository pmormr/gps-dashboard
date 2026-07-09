/**
 * Keep the screen awake while the Drive view is active. The Screen Wake Lock
 * API needs a secure context, which `http://<LAN-IP>:5000` is not — browsers
 * don't even expose `navigator.wakeLock` there — so the production path is the
 * NoSleep-style fallback: a silent, invisible, looping video (a ~1.6 kB bundled
 * asset, inlined by Vite, offline-safe). The real API is used where it exists
 * (localhost dev, a future LAN-HTTPS setup) and re-acquired on tab return,
 * since the browser silently releases it on visibility loss.
 */

import wakelockVideo from '../assets/wakelock.mp4'

let sentinel: WakeLockSentinel | null = null
let video: HTMLVideoElement | null = null
let held = false

async function requestSentinel(): Promise<boolean> {
  try {
    const s = await navigator.wakeLock.request('screen')
    // The browser auto-releases on visibility loss; null the handle so the
    // visibilitychange re-acquire knows it's gone.
    s.addEventListener('release', () => {
      if (sentinel === s) sentinel = null
    })
    sentinel = s
    return true
  } catch {
    return false
  }
}

function onVisibility(): void {
  if (held && sentinel === null && document.visibilityState === 'visible') void requestSentinel()
}

function playVideo(): void {
  if (video) return
  video = document.createElement('video')
  video.muted = true
  video.setAttribute('playsinline', '')
  video.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none'
  video.src = wakelockVideo
  // Seek-back instead of `loop`: some iOS versions pause tiny looping videos,
  // and a video that pauses stops holding the screen awake.
  video.addEventListener('timeupdate', () => {
    if (video && video.currentTime > 0.5) video.currentTime = 0
  })
  document.body.appendChild(video)
  void video.play().catch(() => {
    /* autoplay refused — nothing more we can do without a gesture */
  })
}

/** Hold the screen awake until `releaseWakeLock` (idempotent). */
export async function acquireWakeLock(): Promise<void> {
  if (held) return
  held = true
  if ('wakeLock' in navigator) {
    document.addEventListener('visibilitychange', onVisibility)
    if (await requestSentinel()) return
    document.removeEventListener('visibilitychange', onVisibility)
  }
  playVideo()
}

/** Snapshot of the current hold: which mechanism, and whether it's engaged. */
export interface WakeLockStatus {
  mechanism: 'api' | 'video' | 'none'
  active: boolean
}

/**
 * Report which wake mechanism is in play and whether it's actually holding.
 * The Drive HUD surfaces this so on-device verification (does the fallback
 * video really play on the dash phone?) is a glance, not a wait for the
 * screen to dim. Video "active" = playing; a refused autoplay reads inactive.
 */
export function wakeLockStatus(): WakeLockStatus {
  if (!held) return { mechanism: 'none', active: false }
  if (video) return { mechanism: 'video', active: !video.paused }
  return { mechanism: 'api', active: sentinel !== null }
}

/** Release the hold (idempotent). */
export function releaseWakeLock(): void {
  held = false
  document.removeEventListener('visibilitychange', onVisibility)
  void sentinel?.release()
  sentinel = null
  if (video) {
    video.pause()
    video.remove()
    video = null
  }
}
