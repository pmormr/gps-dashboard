<script lang="ts">
  import { onMount } from 'svelte'

  import { getPointsLatest } from '../lib/api'
  import { CATEGORY_META, clearPlaces, syncPlaces } from '../lib/places'
  import type { TrackPoint } from '../lib/geo'
  import { hookLabels } from '../lib/labels'
  import type { MapView as MapViewType } from '../lib/map'
  import { clearPhone, MODE_COLORS, MODE_LEGEND, syncPhone } from '../lib/phone'
  import { annotations } from '../lib/stores/annotations.svelte'
  import { layers } from '../lib/stores/layers.svelte'
  import { selection } from '../lib/stores/selection.svelte'
  import { track } from '../lib/stores/track.svelte'
  import './map.css'
  import './annotations.css'
  import './places.css'
  import AnnotationForm from './AnnotationForm.svelte'
  import AnnotationsDrawer from './AnnotationsDrawer.svelte'
  import PlaceSheet from './PlaceSheet.svelte'
  import DataLayers from './DataLayers.svelte'
  import InspectPanel from './InspectPanel.svelte'
  import MapStyle from './MapStyle.svelte'
  import MarksPanel from './MarksPanel.svelte'
  import TimeDock from './TimeDock.svelte'

  // Map view: the persistent engine (mapHost.ts) + Svelte chrome. The engine
  // (map.ts + MapLibre, ~283 kB gz) is dynamic-imported so it's a Map-only chunk;
  // the main bundle stays small. `view` is passed to chrome children (Layers) so
  // they never import map.ts directly. Map-local state (Layers/Marks) lives in
  // stores that persist across the cheap chrome remount; the engine itself
  // persists too, so on a fresh load the two agree without reading back. The
  // window fetch itself lives in the shared track store (driven by the TimeDock);
  // this view only *renders* it — trail, annotation overlays, pendingPan.
  let view = $state<typeof MapViewType | undefined>()
  let drawerOpen = $state(false)

  // The right icon rail: exclusive-open panels — opening one
  // closes the rest. Default closed (a clean map); resets on remount. Data layers
  // and map style are separate buttons — overlay toggles and basemap styling
  // aren't related tasks.
  type RailPanel = 'data' | 'style' | 'marks' | 'inspect'
  const RAIL: { id: RailPanel; icon: string; label: string }[] = [
    { id: 'data', icon: '🛰', label: 'Data layers' },
    { id: 'style', icon: '🎨', label: 'Map style' },
    { id: 'marks', icon: '🚩', label: 'Marks' },
    { id: 'inspect', icon: '📊', label: 'Inspect window' },
  ]
  let openPanel = $state<RailPanel | null>(null)
  const railLabel = $derived(RAIL.find((r) => r.id === openPanel)?.label ?? '')

  function toggleRail(id: RailPanel): void {
    openPanel = openPanel === id ? null : id
  }

  // Places detail sheet: the open row id (from a pin click or the Nearby
  // panel), null = closed.
  let placeId = $state<number | null>(null)

  // Places overlay: viewport-driven, so map movement (not the time window)
  // invalidates it. The moveend callback just bumps a revision the sync effect
  // reads; onMoveEnd's callback set makes the repeated add idempotent.
  let placesRev = $state(0)
  function onPlacesMoved(): void {
    placesRev++
  }

  // Viewport filter: while on, the shared track fetch carries
  // the map bbox, so the strip's density shows only time spent in view ("when was
  // I ever at this campsite"). Updates on moveend; resets on toggle-off/unmount.
  let bboxFilter = $state(false)

  function onMoved(): void {
    if (view) track.bbox = view.getBbox()
  }

  function toggleBboxFilter(): void {
    if (!view) return
    bboxFilter = !bboxFilter
    if (bboxFilter) {
      track.bbox = view.getBbox()
      view.onMoveEnd(onMoved)
    } else {
      view.offMoveEnd(onMoved)
      track.bbox = null
    }
  }

  // On-map legend chips (swatch colors mirror DRONE_COLORS in map.ts and
  // MODE_COLORS in phone.ts) — shown only while that layer is on.
  const DRONE_LEGEND = [
    { label: 'Mini 5 Pro', color: '#a855f7' },
    { label: 'Avata 2', color: '#f97316' },
    { label: 'Neo', color: '#ec4899' },
  ]

  onMount(() => {
    let cancelled = false
    let hide: (() => void) | undefined
    Promise.all([import('../lib/mapHost'), import('../lib/map')]).then(([host, mod]) => {
      if (cancelled) return
      view = mod.MapView
      host.showMap()
      hide = host.hideMap
      // Re-apply label settings whenever the vector base (re)loads its style.
      hookLabels(mod.MapView, () => layers.labelSettings)
      // Pin taps open the detail sheet (the engine only hands back the row id).
      mod.MapView.onPlaceClick((id) => (placeId = id))
    })
    annotations.reload()
    return () => {
      cancelled = true
      if (bboxFilter) {
        view?.offMoveEnd(onMoved)
        track.bbox = null
      }
      view?.offMoveEnd(onPlacesMoved)
      view?.clearGhost()
      hide?.()
    }
  })

  // Phone-history overlay follows the global time window: refetch when the window
  // or the toggle changes (and once `view` lands). `range` is only read while the
  // layer is on, so an off overlay doesn't refetch on every scrub.
  $effect(() => {
    if (!view) return
    if (!layers.phone) {
      clearPhone(view)
      return
    }
    const range = selection.range
    syncPhone(view, range.from.toISOString(), range.to.toISOString())
      .then((label) => {
        if (label) layers.phoneStatus = label
      })
      .catch((err) => {
        layers.phoneStatus = `Error: ${err instanceof Error ? err.message : String(err)}`
      })
  })

  // A zoom queued by the Places view's "Show on map" — consume it once the
  // engine is up.
  $effect(() => {
    if (!view || !layers.pendingZoom) return
    const { lat, lon, zoom } = layers.pendingZoom
    layers.pendingZoom = null
    view.zoomTo(lat, lon, zoom)
  })

  // Places overlay follows the *viewport*: refetch when the toggle, the
  // category filter, or the map camera (placesRev) changes — the rank×zoom
  // pin gate applies inside syncPlaces. Categories/rev are only read while
  // the layer is on, so an off overlay costs nothing per pan.
  $effect(() => {
    if (!view) return
    if (!layers.places) {
      view.offMoveEnd(onPlacesMoved)
      clearPlaces(view)
      return
    }
    view.onMoveEnd(onPlacesMoved)
    void placesRev
    const categories = [...layers.placeCategories]
    syncPlaces(view, view.getBbox(), view.getZoom(), categories)
      .then((label) => {
        if (label) layers.placesStatus = label
      })
      .catch((err) => {
        layers.placesStatus = `Error: ${err instanceof Error ? err.message : String(err)}`
      })
  })

  function nearestByTime(points: TrackPoint[], when: Date): TrackPoint | null {
    if (!points.length) return null
    const target = when.getTime()
    let best = points[0]
    let bestDiff = Math.abs(new Date(best.timestamp).getTime() - target)
    for (const p of points) {
      const d = Math.abs(new Date(p.timestamp).getTime() - target)
      if (d < bestDiff) {
        best = p
        bestDiff = d
      }
    }
    return best
  }

  /** Nearest loaded point by time — binary search over the canonical (fixed-width
   * ms-UTC, lexicographically ordered) timestamps, cheap enough for hover-rate calls. */
  function nearestByTimeFast(points: TrackPoint[], ms: number): TrackPoint | null {
    if (!points.length) return null
    const iso = new Date(ms).toISOString()
    let lo = 0
    let hi = points.length - 1
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (points[mid].timestamp < iso) lo = mid + 1
      else hi = mid
    }
    const after = points[lo]
    const before = points[lo - 1]
    if (!before) return after
    const dAfter = Math.abs(new Date(after.timestamp).getTime() - ms)
    const dBefore = Math.abs(new Date(before.timestamp).getTime() - ms)
    return dBefore <= dAfter ? before : after
  }

  // Hover-scrub (time → space): a ghost dot at the hovered strip moment's position.
  $effect(() => {
    if (!view) return
    const ms = track.hoverMs
    const p = ms != null ? nearestByTimeFast(track.points, ms) : null
    if (p) view.setGhost(p.lat, p.lon)
    else view.clearGhost()
  })

  // Trail follows the shared track store. `refit` is false on live continuations
  // (the anchor sliding forward) so the camera stays put.
  $effect(() => {
    if (!view) return
    const pts = track.points
    if (!pts.length) {
      view.clearTrack()
      return
    }
    view.showTrack(pts, { fitBounds: track.refit })
    // A point annotation was just clicked: recentre on its nearest fix now that
    // the reframed window's points have landed.
    if (annotations.pendingPan) {
      const nearest = nearestByTime(pts, annotations.pendingPan)
      annotations.pendingPan = null
      if (nearest) view.zoomTo(nearest.lat, nearest.lon, 14)
    }
  })

  // Annotation overlays (map pins for points, range bands for ranges) against the
  // loaded points. Reactive: runs when the points or the annotation list change
  // (created/deleted/edited — no refetch). Timestamp strings are canonical
  // fixed-width ms-UTC, so lexicographic compares order correctly. The strip's
  // annotation ticks are the TimeDock's job.
  $effect(() => {
    if (!view) return
    view.clearAnnotations()
    const pts = track.points
    if (!pts.length) return
    const winStart = pts[0].timestamp
    const winEnd = pts.at(-1)!.timestamp
    for (const a of annotations.list) {
      if (a.end_time) {
        if (a.start_time > winEnd || a.end_time < winStart) continue
        const segment = pts.filter((p) => p.timestamp >= a.start_time && p.timestamp <= a.end_time!)
        if (segment.length >= 2) view.addRangeOverlay(segment, a.name)
      } else {
        if (a.start_time < winStart || a.start_time > winEnd) continue
        const nearest = nearestByTime(pts, new Date(a.start_time))
        if (nearest) view.addPinOverlay(nearest.lat, nearest.lon, a.name)
      }
    }
  })

  // Recenter the map on the latest *raw* fix (the true current position).
  async function zoomToCurrent(): Promise<void> {
    try {
      const pt = await getPointsLatest()
      if (pt && pt.lat != null && pt.lon != null) view?.zoomTo(pt.lat, pt.lon, 17)
    } catch {
      /* no fix available — ignore */
    }
  }
</script>

<div class="map-region">
  <div class="map-chrome-tl">
    <button class="map-ann-toggle" class:active={drawerOpen} onclick={() => (drawerOpen = !drawerOpen)}>
      <span>📍 Annotations</span>
      {#if annotations.count}<span class="ann-count">{annotations.count}</span>{/if}
    </button>
    <button class="map-fab" title="Zoom to current location" onclick={zoomToCurrent}>⊕</button>

    <!-- On-map legends, visible only while that layer is on. -->
    {#if layers.drone || layers.phone || layers.places}
      <div class="map-legend-chips">
        {#if layers.drone}
          <div class="legend-chip">
            <span class="legend-chip-icon">🚁</span>
            {#each DRONE_LEGEND as m (m.label)}
              <span class="legend-chip-item"><span class="legend-swatch" style:background={m.color}></span>{m.label}</span>
            {/each}
          </div>
        {/if}
        {#if layers.phone}
          <div class="legend-chip">
            <span class="legend-chip-icon">📱</span>
            {#each MODE_LEGEND as m (m.group)}
              <span class="legend-chip-item"><span class="legend-swatch" style:background={MODE_COLORS[m.group]}></span>{m.label}</span>
            {/each}
          </div>
        {/if}
        {#if layers.places}
          <div class="legend-chip">
            <span class="legend-chip-icon">🏞</span>
            {#each CATEGORY_META.filter((c) => layers.placeCategories.has(c.category)) as c (c.category)}
              <span class="legend-chip-item"><span class="legend-swatch" style:background={c.color}></span>{c.label}</span>
            {/each}
          </div>
        {/if}
      </div>
    {/if}
  </div>

  <div class="map-rail">
    {#each RAIL as r (r.id)}
      <button
        type="button"
        class:active={openPanel === r.id}
        title={r.label}
        aria-label={r.label}
        onclick={() => toggleRail(r.id)}
      >{r.icon}</button>
    {/each}
    <div class="map-rail-sep"></div>
    <button
      type="button"
      class:active={bboxFilter}
      title="Filter time density to the current map view"
      aria-label="Filter time density to the current map view"
      onclick={toggleBboxFilter}
    >⛶</button>
  </div>

  {#if openPanel}
    <div class="rail-card">
      <div class="rail-card-hdr">
        <span>{railLabel}</span>
        <button type="button" aria-label="Close panel" onclick={() => (openPanel = null)}>✕</button>
      </div>
      <div class="rail-card-body">
        {#if openPanel === 'data'}
          <DataLayers {view} />
        {:else if openPanel === 'style'}
          <MapStyle {view} />
        {:else if openPanel === 'marks'}
          <MarksPanel />
        {:else}
          <InspectPanel />
        {/if}
      </div>
    </div>
  {/if}

  <div class="tl-overlay">
    <TimeDock />
  </div>
</div>

<AnnotationsDrawer bind:open={drawerOpen} />
<AnnotationForm />
{#if placeId != null}
  <PlaceSheet id={placeId} {view} onClose={() => (placeId = null)} />
{/if}
