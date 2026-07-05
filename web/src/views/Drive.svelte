<script lang="ts">
  import { onMount } from 'svelte'

  import { getPointsRecent } from '../lib/api'
  import {
    ENTER_EASE_MS,
    FOLLOW_PITCH_DEG,
    slew,
    speedZoom,
    ZOOM_SLEW_PER_S,
  } from '../lib/follow'
  import { fmtAltitude, haversineMeters } from '../lib/geo'
  import { cardinal, type Crumb, extendCrumbs } from '../lib/live'
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

  // Breadcrumb: seeded from the raw trailing window, extended from live fixes
  // (extendCrumbs gates on movement, so a parked van doesn't grow a fuzzball).
  // Plain array — the map render is driven by the fix effect, not reactivity.
  const TRAIL_MINUTES = 30
  const TRAIL_SEED_LIMIT = 500
  let crumbs: Crumb[] = []
  let lastCrumbFixTime = ''

  const STATUS_LABELS: Record<string, string> = {
    connecting: 'Connecting…',
    'no-fix': 'No GPS fix',
    stale: 'Stale fix',
    offline: 'GPS offline',
  }
  const statusLabel = $derived(live.status === 'ok' ? '' : STATUS_LABELS[live.status])

  // HUD readouts, from the raw fix (interpolation would just add display lag).
  const mph = $derived(
    live.fix?.speed != null && live.status !== 'no-fix' && live.status !== 'offline'
      ? Math.round(live.fix.speed * 2.23694)
      : null,
  )
  const hdg = $derived(live.heading != null ? Math.round(live.heading) % 360 : null)
  const altLabel = $derived(fmtAltitude(live.fix?.alt ?? null))

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
      if (crumbs.length) mod.MapView.setBreadcrumb(crumbs)
    })
    // Seed the trail from the raw tier so it isn't empty on view open (the
    // processed tier lags the processor's cursor — plan trap 3). Live fixes
    // extend it from there; seed/live overlap is deduped by the movement gate.
    getPointsRecent(TRAIL_MINUTES, TRAIL_SEED_LIMIT)
      .then((resp) => {
        if (cancelled) return
        const seed = resp.points.map((p) => ({ lat: p.lat, lon: p.lon, t: Date.parse(p.timestamp) }))
        crumbs = [...seed, ...crumbs.filter((c) => c.t > (seed.at(-1)?.t ?? -Infinity))]
        view?.setBreadcrumb(crumbs)
      })
      .catch(() => {
        /* no seed — the trail still builds from live fixes */
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
        view.clearBreadcrumb()
        // Hand the camera back the way Map expects it: flat north-up (60° if
        // the 3D toggle is on). Map's own track effect refits on remount.
        view.easeCamera({ pitch: view.getTerrainEnabled() ? 60 : 0, bearing: 0 })
      }
      hide?.()
    }
  })

  // Trail extension: one attempt per fresh fix (keyed by TPV time, not per rAF).
  $effect(() => {
    const fix = live.fix
    if (!view || !fix || fix.lat == null || fix.lon == null) return
    if (live.status !== 'ok' && live.status !== 'stale') return
    if (!fix.time || fix.time === lastCrumbFixTime) return
    lastCrumbFixTime = fix.time
    if (extendCrumbs(crumbs, fix.lat, fix.lon, Date.parse(fix.time), haversineMeters)) {
      view.setBreadcrumb(crumbs)
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

  <div class="drive-hud">
    <div class="drive-hud-speed">
      <span class="num">{mph ?? '–'}</span>
      <span class="unit">mph</span>
    </div>
    <div class="drive-hud-cell">
      <span class="k">HDG</span>
      <span class="v">{hdg != null ? `${cardinal(hdg)} ${hdg}°` : '—'}</span>
    </div>
    <div class="drive-hud-cell">
      <span class="k">ALT</span>
      <span class="v">{altLabel}</span>
    </div>
  </div>
</div>
