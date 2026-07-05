<script lang="ts">
  import { onMount } from 'svelte'

  import {
    ENTER_EASE_MS,
    FOLLOW_PITCH_DEG,
    slew,
    speedZoom,
    ZOOM_SLEW_PER_S,
  } from '../lib/follow'
  import type { MapView as MapViewType } from '../lib/map'
  import { live } from '../lib/stores/live.svelte'
  import { acquireWakeLock, releaseWakeLock } from '../lib/wakelock'
  import './map.css'
  import './drive.css'

  // Drive view: the shared map engine (mapHost.ts) under driving chrome. The
  // live store owns polling + rAF interpolation; this view reacts to its
  // pos/heading and hard-sets the camera each frame (the store's interpolation
  // is the easing). Data layers (track, drone, phone…) are left as the user
  // set them — Drive is the same map with a different camera.
  let view = $state<typeof MapViewType | undefined>()
  /** A gesture took the camera; follow resumes via the recenter pill. */
  let suspended = $state(false)

  // Follow-loop scratch (plain lets — per-frame mutation must not retrigger the effect).
  let zoom = speedZoom(null)
  let lastFrameMs = 0
  let followingSinceMs = 0

  const STATUS_LABELS: Record<string, string> = {
    connecting: 'Connecting…',
    'no-fix': 'No GPS fix',
    stale: 'Stale fix',
    offline: 'GPS offline',
  }
  const statusLabel = $derived(live.status === 'ok' ? '' : STATUS_LABELS[live.status])

  function onGesture(): void {
    suspended = true
  }

  function recenter(): void {
    suspended = false
    followingSinceMs = 0
    lastFrameMs = 0
  }

  onMount(() => {
    let cancelled = false
    let hide: (() => void) | undefined
    Promise.all([import('../lib/mapHost'), import('../lib/map')]).then(([host, mod]) => {
      if (cancelled) return
      view = mod.MapView
      host.showMap()
      hide = host.hideMap
      mod.MapView.onUserMove(onGesture)
    })
    live.start()
    void acquireWakeLock()
    return () => {
      cancelled = true
      live.stop()
      releaseWakeLock()
      if (view) {
        view.offUserMove(onGesture)
        view.clearPuck()
        // Hand the camera back the way Map expects it: flat north-up (60° if
        // the 3D toggle is on). Map's own track effect refits on remount.
        view.easeCamera({ pitch: view.getTerrainEnabled() ? 60 : 0, bearing: 0 })
      }
      hide?.()
    }
  })

  // Follow loop, driven by the store's rAF-interpolated position. On (re)entry
  // it eases into the follow pose, then hard-sets the camera per frame — but
  // not during the ease, since a jumpTo would cancel it.
  $effect(() => {
    const pos = live.pos
    if (!view || !pos) return
    view.setPuck(pos.lat, pos.lon, live.heading)
    if (suspended) return

    const now = performance.now()
    const bearing = live.heading ?? 0
    const target = speedZoom(live.fix?.speed ?? null)
    if (followingSinceMs === 0) {
      followingSinceMs = now
      zoom = target
      view.easeCamera({
        lat: pos.lat,
        lon: pos.lon,
        bearing,
        pitch: FOLLOW_PITCH_DEG,
        zoom,
        duration: ENTER_EASE_MS,
      })
      return
    }
    if (now - followingSinceMs < ENTER_EASE_MS) return

    const dtS = lastFrameMs ? (now - lastFrameMs) / 1000 : 0
    lastFrameMs = now
    zoom = slew(zoom, target, ZOOM_SLEW_PER_S * dtS)
    view.setCamera({ lat: pos.lat, lon: pos.lon, bearing, pitch: FOLLOW_PITCH_DEG, zoom })
  })
</script>

<div class="map-region">
  {#if statusLabel}
    <div class="drive-status" class:drive-status--bad={live.status === 'offline' || live.status === 'no-fix'}>
      {statusLabel}
    </div>
  {/if}

  {#if suspended}
    <button type="button" class="drive-recenter" onclick={recenter}>⌖ Recenter</button>
  {/if}
</div>
