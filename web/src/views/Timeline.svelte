<script lang="ts">
  import { onMount } from 'svelte'

  import { getPoints } from '../lib/api'
  import type { TrackPoint } from '../lib/geo'
  import type { MapView as MapViewType } from '../lib/map'
  import { annotations } from '../lib/stores/annotations.svelte'
  import { selection, type Range } from '../lib/stores/selection.svelte'
  import { mountTimeStrip, type TimeStripHandle } from '../lib/timestrip'
  import './timeline.css'
  import TimePicker from './TimePicker.svelte'

  // The Selection-axis chrome (ported from timeline.js): the window picker, the
  // TimeStrip brush, and the trail render for the selection. Drives the
  // kept-imperative TimeStrip via setData/getSelection/onBrush and the map via the
  // passed MapView façade (`view` is a prop so this component never imports map.ts
  // — that would pull MapLibre into the main bundle). It also renders the
  // annotation overlays (pins/range bands + strip ticks) against the loaded points.
  let { view }: { view?: typeof MapViewType } = $props()

  let canvas: HTMLCanvasElement
  let tooltip: HTMLElement
  let strip: TimeStripHandle | undefined

  let allPoints = $state<TrackPoint[]>([])
  let status = $state('')
  let empty = $state('')
  let hasData = $state(false)
  let selCount = $state(0)
  let canZoom = $state(false)
  let canCreate = $state(false)
  let isLive = $state(false)
  let bookmarked = $state(false)
  let startLabel = $state('')
  let endLabel = $state('')
  let lastRange: Range | null = null
  let fetchToken = 0

  function windowLabel(ms: number): string {
    return new Date(ms).toLocaleString([], {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  // A moving vertex is an instant; a stop spans its dwell interval (match by
  // overlap), so brushing inside a long park selects "parked here" (S2).
  function pointsInRange(loMs: number, hiMs: number): TrackPoint[] {
    return allPoints.filter((p) => {
      if (p.kind === 'stop' && p.dwell_start && p.dwell_end) {
        return new Date(p.dwell_end).getTime() >= loMs && new Date(p.dwell_start).getTime() <= hiMs
      }
      const t = new Date(p.timestamp).getTime()
      return t >= loMs && t <= hiMs
    })
  }

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

  // Render the current brush: trail/map, the count, and the Zoom/Create gating.
  // Map-local only — the store no longer carries a brush (the strip's two-handle
  // brush dies entirely in time-dock Phase 2). followMap fits on initial load.
  function renderRange(followMap: boolean): void {
    const sel = strip?.getSelection()
    if (!sel || !view || !lastRange) return
    startLabel = windowLabel(sel.loMs)
    endLabel = windowLabel(sel.hiMs)
    const pts = pointsInRange(sel.loMs, sel.hiMs)
    const isSub = sel.loMs > lastRange.from.getTime() || sel.hiMs < lastRange.to.getTime()
    view.showTrack(pts, { fitBounds: followMap, showEndpoints: isSub && pts.length > 1 })
    selCount = pts.length
    canZoom = isSub
    canCreate = pts.length >= 2
  }

  // Re-draw the annotation overlays (map pins for points, range bands for ranges)
  // + the strip's annotation ticks against the loaded window. Reactive: runs when
  // the points or the annotation list change (created/deleted/edited — no refetch).
  // Timestamp strings are canonical fixed-width ms-UTC, so lexicographic compares
  // order correctly.
  function renderAnnotationOverlays(): void {
    if (!view) return
    view.clearAnnotations()
    strip?.setAnnotations(annotations.list)
    const pts = allPoints
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
  }

  async function loadRange(range: Range): Promise<void> {
    if (!view) return
    const { from, to, live } = range
    isLive = live
    // Live re-emits (anchor sliding forward) are continuations — don't refit each tick.
    const isLiveTick =
      !!lastRange &&
      lastRange.live &&
      live &&
      lastRange.mode === range.mode &&
      lastRange.windowMs === range.windowMs
    lastRange = range
    status = 'Loading…'
    empty = ''
    const token = ++fetchToken
    let data
    try {
      data = await getPoints(from.toISOString(), to.toISOString(), 20000)
    } catch (e) {
      if (token === fetchToken) status = `Error: ${e instanceof Error ? e.message : String(e)}`
      return
    }
    if (token !== fetchToken) return // superseded by a newer window
    allPoints = data.points
    status = `${allPoints.length.toLocaleString()} pts${data.truncated ? ' (truncated)' : ''}`
    if (!allPoints.length) {
      hasData = false
      empty = 'No GPS points for this range'
      view.clearTrack()
      canZoom = false
      canCreate = false
      return
    }
    hasData = true
    // The axis is the requested window, not the data extent (S1): empty leading/
    // trailing time stays visible; data renders onto it via the TimeStrip canvas.
    strip?.setData({ startMs: from.getTime(), endMs: to.getTime(), points: allPoints })
    renderRange(!isLiveTick)
    // A point annotation was just clicked: recentre on its nearest fix now that
    // the reframed window's points have landed.
    if (annotations.pendingPan) {
      const nearest = nearestByTime(allPoints, annotations.pendingPan)
      annotations.pendingPan = null
      if (nearest) view.zoomTo(nearest.lat, nearest.lon, 14)
    }
  }

  // Narrow the loaded window to the brush and re-fetch — same vertex budget over a
  // smaller window yields more detail.
  function zoomToRange(): void {
    const sel = strip?.getSelection()
    if (!sel || sel.hiMs <= sel.loMs) return
    selection.setRange(new Date(sel.loMs), new Date(sel.hiMs))
  }

  // Open the create form for the current brush (≥2 points → a range annotation).
  function createRange(): void {
    const sel = strip?.getSelection()
    if (!sel || !canCreate) return
    annotations.openCreate(new Date(sel.loMs).toISOString(), new Date(sel.hiMs).toISOString())
  }

  async function bookmarkHere(): Promise<void> {
    if (bookmarked) return
    try {
      await annotations.bookmarkCurrent()
      bookmarked = true
      setTimeout(() => (bookmarked = false), 1200)
    } catch (e) {
      alert(`Bookmark failed: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  onMount(() => {
    strip = mountTimeStrip(canvas, tooltip)
    strip.onBrush(() => renderRange(false))
    return () => strip?.destroy()
  })

  // Refetch whenever the global window changes (also fires once `view` arrives, and
  // on each live tick).
  $effect(() => {
    const range = selection.range
    if (view) loadRange(range)
  })

  // Re-draw annotation overlays whenever the points or the annotation list change.
  $effect(() => {
    void annotations.list
    void allPoints
    if (view) renderAnnotationOverlays()
  })
</script>

<div class="timeline">
  <div class="tl-time-row">
    <TimePicker />
    <span class="tl-status">{status}</span>
  </div>

  <div class="tl-strip-wrap" class:hidden={!hasData}>
    <div class="tl-strip-canvas-wrap">
      <canvas class="tl-strip" bind:this={canvas}></canvas>
      <div class="tl-strip-tooltip hidden" bind:this={tooltip}></div>
    </div>
    <div class="tl-slider-labels">
      <span>{startLabel}</span>
      <span class="muted">{selCount} points selected</span>
      <span>{endLabel}</span>
    </div>
  </div>

  {#if empty}<p class="tl-empty muted">{empty}</p>{/if}

  <div class="tl-bottom-actions">
    <button class="btn-secondary" disabled={!canZoom} onclick={zoomToRange}>Zoom to Range</button>
    {#if isLive}
      <button class="btn-secondary" disabled={bookmarked} onclick={bookmarkHere}>
        {bookmarked ? '✓ Bookmarked' : '📍 Bookmark Here'}
      </button>
    {/if}
    <button class="btn-primary" disabled={!canCreate} onclick={createRange}>Create Range</button>
  </div>
</div>
