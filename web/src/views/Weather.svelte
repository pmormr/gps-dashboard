<script lang="ts">
  import { onMount } from 'svelte'

  import { getGpsdLive } from '../lib/api'
  import type { MapView as MapViewType, RadarLoadStats } from '../lib/map'
  import {
    clearRadar,
    clearWarnings,
    formatBytes,
    frameAgeLabel,
    frameDateLabel,
    frameTimeLabel,
    loadRadar,
    loadWarnings,
    WINDOW_PRESETS,
  } from '../lib/weather'
  import './map.css'
  import './weather.css'

  // Weather view: the shared map engine (mapHost.ts) under an animated radar
  // overlay. weather.ts loads a recent frame window as per-frame PMTiles raster
  // sources through the MapView façade; this view owns the playback clock and the
  // scrubber/opacity/window chrome. Radar renders below basemap labels.
  let view = $state<typeof MapViewType | undefined>()

  const SPEEDS = [
    { label: '1×', ms: 600 },
    { label: '2×', ms: 300 },
    { label: '4×', ms: 150 },
  ]

  let frames = $state<number[]>([]) // ascending (old → new)
  let index = $state(0)
  let playing = $state(false) // open on "now"; Play animates the loop
  let speedIdx = $state(1)
  let opacity = $state(0.8)
  let windowHours = $state(3)
  let loading = $state(true)
  let error = $state('')
  let nowMs = $state(Date.now())
  let warningsOn = $state(true)
  let warningsCount = $state(0)
  // Tile-load readout, pushed by the instrumented pmtiles protocol (map.ts):
  // pending = radar tile reads in flight, tiles/bytes = downloaded this visit.
  let loadStats = $state<RadarLoadStats>({ pending: 0, tiles: 0, bytes: 0 })
  const onLoadStats = (stats: RadarLoadStats): void => {
    loadStats = stats
  }

  const current = $derived(frames.length ? frames[index] : null)
  const newest = $derived(frames.length ? frames[frames.length - 1] : null)

  async function loadWindow(hours: number): Promise<void> {
    if (!view) return
    loading = true
    error = ''
    try {
      const loaded = await loadRadar(view, hours)
      frames = loaded
      index = loaded.length ? loaded.length - 1 : 0 // show newest first
      playing = false
    } catch {
      error = 'Radar unavailable'
      frames = []
    } finally {
      loading = false
    }
  }

  function pickWindow(hours: number): void {
    if (hours === windowHours) return
    windowHours = hours
    void loadWindow(hours)
  }

  function togglePlay(): void {
    // Starting playback from the newest frame would immediately wrap to the
    // oldest; restart the loop at the oldest so it reads forward to "now".
    if (!playing && index >= frames.length - 1) index = 0
    playing = !playing
  }

  function onScrub(e: Event): void {
    playing = false
    index = Number((e.currentTarget as HTMLInputElement).value)
  }

  async function refreshWarnings(): Promise<void> {
    if (!view) return
    if (!warningsOn) {
      clearWarnings(view)
      warningsCount = 0
      return
    }
    try {
      warningsCount = (await loadWarnings(view, Date.now())).count
    } catch {
      warningsCount = 0
    }
  }

  function toggleWarnings(): void {
    warningsOn = !warningsOn
    void refreshWarnings()
  }

  async function centerOnVan(): Promise<void> {
    try {
      const fix = await getGpsdLive()
      if (fix.lat != null && fix.lon != null) view?.zoomTo(fix.lat, fix.lon, 7)
    } catch {
      /* no fix — leave the camera where it is */
    }
  }

  onMount(() => {
    let cancelled = false
    let hide: (() => void) | undefined
    Promise.all([import('../lib/mapHost'), import('../lib/map')]).then(([host, mod]) => {
      if (cancelled) return
      view = mod.MapView
      host.showMap()
      hide = host.hideMap
      view.onRadarLoad(onLoadStats)
      void loadWindow(windowHours)
      void refreshWarnings()
    })
    const nowTimer = setInterval(() => (nowMs = Date.now()), 30_000)
    return () => {
      cancelled = true
      clearInterval(nowTimer)
      if (view) {
        view.offRadarLoad(onLoadStats)
        clearRadar(view)
        clearWarnings(view)
      }
      hide?.()
    }
  })

  // Advance the loop while playing. Reads playing/speed/length (not index), so it
  // restarts on those changes without looping on its own writes.
  $effect(() => {
    if (!playing || frames.length < 2) return
    const ms = SPEEDS[speedIdx].ms
    const timer = setInterval(() => (index = (index + 1) % frames.length), ms)
    return () => clearInterval(timer)
  })

  // Render the current frame — driven by both auto-advance and scrubbing.
  $effect(() => {
    if (view && frames.length) view.showRadarFrame(frames[index])
  })

  // Push opacity through to the raster layers.
  $effect(() => {
    view?.setRadarOpacity(opacity)
  })
</script>

<div class="map-region">
  <div class="wx-status">
    {#if loading}
      <span class="wx-status-line">Loading radar…</span>
    {:else if error}
      <span class="wx-status-line wx-status-line--bad">{error}</span>
    {:else if !frames.length}
      <span class="wx-status-line">No radar archived yet</span>
      <span class="wx-status-sub">Frames capture while the van is online.</span>
    {:else}
      <span class="wx-status-line">Radar · base reflectivity</span>
      <span class="wx-status-sub">
        {newest != null ? frameAgeLabel(newest, nowMs) : '—'} · {frames.length} frames
      </span>
    {/if}
  </div>

  {#if frames.length}
    <div class="wx-panel">
      <div class="wx-scrubline">
        <button
          type="button"
          class="wx-play"
          onclick={togglePlay}
          aria-label={playing ? 'Pause' : 'Play'}
        >
          {playing ? '❚❚' : '▶'}
        </button>
        <input
          class="wx-scrub"
          type="range"
          min="0"
          max={frames.length - 1}
          value={index}
          oninput={onScrub}
          aria-label="Radar time"
        />
      </div>

      <div class="wx-readout">
        <span class="wx-time">{current != null ? frameTimeLabel(current) : '—'}</span>
        <span class="wx-date">{current != null ? frameDateLabel(current) : ''}</span>
        {#if loadStats.pending || loadStats.bytes}
          <span
            class="wx-load"
            class:wx-load--busy={loadStats.pending > 0}
            title="Radar tiles — loading now · downloaded since opening Weather"
          >
            {#if loadStats.pending}↓{loadStats.pending} · {/if}{formatBytes(loadStats.bytes)}
          </span>
        {/if}
        <span class="wx-frameno">{index + 1}/{frames.length}</span>
      </div>

      <div class="wx-controls">
        <div class="wx-group" role="group" aria-label="Window">
          {#each WINDOW_PRESETS as h (h)}
            <button
              type="button"
              class="wx-chip"
              class:wx-chip--on={windowHours === h}
              onclick={() => pickWindow(h)}
              title={`Load the last ${h} hour${h === 1 ? '' : 's'} of radar frames`}
            >
              {h}h
            </button>
          {/each}
        </div>

        <button
          type="button"
          class="wx-chip"
          onclick={() => (speedIdx = (speedIdx + 1) % SPEEDS.length)}
          aria-label="Playback speed"
          title="Playback speed"
        >
          {SPEEDS[speedIdx].label}
        </button>

        <label class="wx-opacity" title="Radar opacity">
          <span aria-hidden="true">◐</span>
          <input type="range" min="0.2" max="1" step="0.05" bind:value={opacity} aria-label="Radar opacity" />
        </label>

        <button
          type="button"
          class="wx-chip"
          class:wx-chip--on={warningsOn}
          onclick={toggleWarnings}
          aria-label="Toggle watches & warnings"
          title={warningsOn
            ? `NWS watches & warnings — ${warningsCount} active (click to hide)`
            : 'Show NWS watches & warnings'}
        >
          ⚠{warningsCount ? ` ${warningsCount}` : ''}
        </button>

        <button type="button" class="wx-chip" onclick={centerOnVan} title="Center the map on the van">
          ⌖ Van
        </button>
      </div>
    </div>
  {/if}
</div>
