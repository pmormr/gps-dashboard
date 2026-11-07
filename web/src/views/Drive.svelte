<script lang="ts">
  import { onMount } from 'svelte'

  import Sparkline from '../lib/charts/Sparkline.svelte'
  import {
    type ElevationResponse,
    getPointsElevation,
    getPointsRecent,
    getStatus,
    type Status,
  } from '../lib/api'
  import {
    ENTER_EASE_MS,
    FOLLOW_PITCH_DEG,
    LABEL_SCALE,
    slew,
    speedZoom,
    ZOOM_SLEW_PER_S,
  } from '../lib/follow'
  import {
    celsiusToF,
    fmtAltitude,
    fmtDistance,
    fmtTime,
    haversineMeters,
    initialBearingDeg,
    mpsToMph,
  } from '../lib/geo'
  import { fmtPopulation, type Here, placeLabel } from '../lib/compass'
  import { cardinal, type Crumb, extendCrumbs } from '../lib/live'
  import { elevationRecords } from '../lib/records'
  import type { MapView as MapViewType } from '../lib/map'
  import { destination } from '../lib/stores/destination.svelte'
  import { live } from '../lib/stores/live.svelte'
  import {
    acquireWakeLock,
    releaseWakeLock,
    wakeLockStatus,
    type WakeLockStatus,
  } from '../lib/wakelock'
  import DriveCompass from './DriveCompass.svelte'
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
  /** The current town, bound out of the compass for the HUD's centre readout. */
  let here = $state<Here | null>(null)

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

  // Wake-lock readout: polled (the mechanism state lives outside Svelte), so a
  // paused fallback video or a released sentinel is visible on the dash.
  let wake = $state<WakeLockStatus>({ mechanism: 'none', active: false })
  const wakeLabel = $derived(
    wake.active ? (wake.mechanism === 'api' ? 'WAKE API' : 'WAKE VID') : 'WAKE ✗',
  )

  // OBD strip: /api/status polled at ~5 s (decision 7 — ingest cadence bounds
  // freshness, not the HTTP poll). Shown only engine-on: link up, reading fresh
  // against the server's own clock, rpm > 0. The reader publishes ~1 s snapshots
  // while the engine runs, so a 30 s gate hides the strip promptly after key-off.
  const OBD_POLL_MS = 5000
  const OBD_FRESH_MS = 30_000
  let obdStatus = $state<Status | null>(null)
  const van = $derived.by(() => {
    const s = obdStatus
    if (!s?.van || s.obd_link !== 'online') return null
    if (Date.parse(s.now) - Date.parse(s.van.timestamp) > OBD_FRESH_MS) return null
    return s.van.rpm != null && s.van.rpm > 0 ? s.van : null
  })
  const coolF = $derived(van?.coolant_c != null ? Math.round(celsiusToF(van.coolant_c)) : null)
  const fuelPct = $derived(van?.fuel_level_pct != null ? Math.round(van.fuel_level_pct) : null)
  const gph = $derived(
    van?.fuel_rate_lph != null ? (van.fuel_rate_lph * 0.264172).toFixed(1) : null,
  )

  // Destination chevron: great-circle distance + bearing relative to course.
  // Off the raw fix like the other readouts. rel is null without a heading
  // (parked, no course yet); the HUD falls back to the absolute cardinal then.
  const destInfo = $derived.by(() => {
    const d = destination.current
    const fix = live.fix
    if (!d || !fix || fix.lat == null || fix.lon == null) return null
    const meters = haversineMeters(fix.lat, fix.lon, d.lat, d.lon)
    const bearing = initialBearingDeg(fix.lat, fix.lon, d.lat, d.lon)
    const rel = live.heading != null ? (bearing - live.heading + 360) % 360 : null
    return { meters, bearing, rel }
  })

  function onLongPress(lat: number, lon: number): void {
    // A dropped pin is a raw-coords destination; saving pins as places belongs
    // to the trip planner, not Drive.
    destination.set({ name: null, lat, lon })
  }

  // HUD readouts, from the raw fix (interpolation would just add display lag).
  const mph = $derived(
    live.fix?.speed != null && live.status !== 'no-fix' && live.status !== 'offline'
      ? Math.round(mpsToMph(live.fix.speed))
      : null,
  )
  const hdg = $derived(live.heading != null ? Math.round(live.heading) % 360 : null)
  const altLabel = $derived(fmtAltitude(live.fix?.alt ?? null))

  // The behind-compass strip sits on the HUD's top edge, whose height varies
  // with the engine-gated OBD row — measured, not assumed.
  let hudHeight = $state(0)

  // Records: today's elevation profile (server buckets, 30 s poll) folded
  // with the live fix each tick, so a new high shows the second it happens.
  const ELEVATION_POLL_MS = 30_000
  let elevation = $state<ElevationResponse | null>(null)
  const records = $derived(
    elevationRecords(
      elevation?.buckets ?? [],
      live.status === 'ok' || live.status === 'stale' ? (live.fix?.alt ?? null) : null,
      live.fix?.time ?? null,
      Date.now(),
    ),
  )
  const sinceLabel = $derived(
    elevation == null
      ? ''
      : elevation.start_reason === 'stop'
        ? `since ${fmtTime(elevation.start)}`
        : 'last 24 h',
  )

  function onGesture(): void {
    suspended = true
  }

  function recenter(): void {
    suspended = false
    followingSinceMs = 0
    lastFrameMs = 0
  }

  // Fullscreen toggle: gets the browser chrome (URL bar) off a dash-mounted
  // phone/tablet. The button only renders where the API exists — iPhone Safari
  // offers fullscreen for <video> alone, so there it stays hidden.
  const fullscreenSupported =
    typeof document !== 'undefined' &&
    document.fullscreenEnabled === true &&
    typeof document.documentElement.requestFullscreen === 'function'
  let fullscreen = $state(false)

  function toggleFullscreen(): void {
    if (document.fullscreenElement) void document.exitFullscreen()
    else void document.documentElement.requestFullscreen().catch(() => {})
  }

  $effect(() => {
    const sync = (): void => {
      fullscreen = !!document.fullscreenElement
    }
    sync()
    document.addEventListener('fullscreenchange', sync)
    return () => document.removeEventListener('fullscreenchange', sync)
  })

  onMount(() => {
    let cancelled = false
    let hide: (() => void) | undefined
    Promise.all([import('../lib/mapHost'), import('../lib/map')]).then(([host, mod]) => {
      if (cancelled) return
      view = mod.MapView
      host.showMap()
      hide = host.hideMap
      mod.MapView.onUserMove(onGesture)
      mod.MapView.onLongPress(onLongPress)
      mod.MapView.setLabelScale(LABEL_SCALE)
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
    void acquireWakeLock().then(() => {
      wake = wakeLockStatus()
    })
    const wakeTimer = setInterval(() => {
      wake = wakeLockStatus()
    }, 2000)
    // The server-clock freshness gate only ages readings while polls *succeed*
    // (a frozen snapshot freezes its `now` too), so sustained poll failure
    // drops the snapshot after the same window.
    let obdOkMs = 0
    const pollObd = (): void => {
      getStatus()
        .then((s) => {
          if (cancelled) return
          obdOkMs = performance.now()
          obdStatus = s
        })
        .catch(() => {
          if (!cancelled && performance.now() - obdOkMs > OBD_FRESH_MS) obdStatus = null
        })
    }
    pollObd()
    const obdTimer = setInterval(pollObd, OBD_POLL_MS)
    const pollElevation = (): void => {
      getPointsElevation()
        .then((e) => {
          if (!cancelled) elevation = e
        })
        .catch(() => {
          /* keep the last profile; the live fix still drives the records */
        })
    }
    pollElevation()
    const elevationTimer = setInterval(pollElevation, ELEVATION_POLL_MS)
    return () => {
      cancelled = true
      live.stop()
      clearInterval(wakeTimer)
      clearInterval(obdTimer)
      clearInterval(elevationTimer)
      releaseWakeLock()
      if (view) {
        view.offUserMove(onGesture)
        view.offLongPress(onLongPress)
        view.clearPuck()
        view.clearBreadcrumb()
        view.clearDestination()
        view.setLabelScale(1)
        // Hand the camera back the way Map expects it: flat north-up (60° if
        // the 3D toggle is on). Map's own track effect refits on remount.
        // duration 0 — an animated ease dies here: Map mounts right after and
        // its first camera command stop()s the in-flight ease, freezing the
        // pitch mid-restore (~50°). The handoff is off-screen anyway.
        view.easeCamera({ pitch: view.getTerrainEnabled() ? 60 : 0, bearing: 0, duration: 0 })
      }
      hide?.()
    }
  })

  // Destination pin follows the store (set from here, Places, or a reload).
  $effect(() => {
    const d = destination.current
    if (!view) return
    if (d) view.setDestination(d.lat, d.lon)
    else view.clearDestination()
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

  <DriveCompass
    lat={live.fix?.lat ?? null}
    lon={live.fix?.lon ?? null}
    heading={live.heading}
    bottomPx={hudHeight}
    bind:here
  />

  <div class="drive-hud" bind:clientHeight={hudHeight}>
    {#if van}
      <div class="drive-obd">
        <div class="drive-hud-cell">
          <span class="k">RPM</span>
          <span class="v">{van.rpm != null ? Math.round(van.rpm) : '—'}</span>
        </div>
        <div class="drive-hud-cell">
          <span class="k">COOLANT</span>
          <span class="v">{coolF != null ? `${coolF}°F` : '—'}</span>
        </div>
        <div class="drive-hud-cell">
          <span class="k">FUEL</span>
          <span class="v">{fuelPct != null ? `${fuelPct}%` : '—'}</span>
        </div>
        <div class="drive-hud-cell">
          <span class="k">GPH</span>
          <span class="v">{gph ?? '—'}</span>
        </div>
      </div>
    {/if}
    <div class="drive-hud-main">
      <div class="drive-hud-speed">
        <div class="drive-hud-speed-row">
          <span class="num">{mph ?? '–'}</span>
          <span class="unit">mph</span>
        </div>
        <span class="drive-hud-hdg">
          {hdg != null ? `${cardinal(hdg)} ${hdg}°` : '—'}
          <span class="drive-wake" class:drive-wake--bad={!wake.active}>· {wakeLabel}</span>
        </span>
      </div>
      <div class="drive-hud-here">
        {#if here}
          <span class="k">{here.inside ? 'IN' : 'NEAR'}</span>
          <span class="v">{placeLabel(here.place)}</span>
          <span class="pop" data-tier={here.place.tier}>{fmtPopulation(here.place.population)}</span>
        {/if}
      </div>
      {#if destination.current}
        <button
          type="button"
          class="drive-hud-cell drive-dest"
          title="Clear destination"
          onclick={() => destination.clear()}
        >
          <span class="k">{destination.current.name ?? 'PIN'} ✕</span>
          <span class="v">
            {#if destInfo}
              {#if destInfo.rel != null}
                <span class="drive-dest-arrow" style:transform={`rotate(${destInfo.rel}deg)`}
                  >▲</span>
              {:else}
                <span class="drive-dest-cardinal">{cardinal(destInfo.bearing)}</span>
              {/if}
              {fmtDistance(destInfo.meters)}
            {:else}
              —
            {/if}
          </span>
        </button>
      {/if}
      <div class="drive-hud-cell drive-alt">
        <span class="k">ALT</span>
        <span class="v">{altLabel}</span>
        <Sparkline values={records.spark} color="#4ade80" width={96} height={16} />
      </div>
      {#if elevation}
        <div class="drive-hud-cell drive-today" title={sinceLabel}>
          <span class="k">TODAY {sinceLabel.replace('since ', '')}</span>
          <span class="rec rec-hi">▲ {fmtAltitude(records.high?.altitudeM ?? null)}</span>
          <span class="rec rec-lo">▼ {fmtAltitude(records.low?.altitudeM ?? null)}</span>
        </div>
      {/if}
      {#if fullscreenSupported}
        <button
          type="button"
          class="drive-hud-btn"
          title={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
          aria-label={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
          aria-pressed={fullscreen}
          onclick={toggleFullscreen}
        >{fullscreen ? '⤡' : '⛶'}</button>
      {/if}
    </div>
  </div>
</div>
