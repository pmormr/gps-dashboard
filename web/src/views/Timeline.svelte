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

  // The Selection-axis chrome: the window picker + nav cluster, the TimeStrip
  // (drag-to-zoom — every gesture commits to the Selection store), and the trail
  // render for the window. The trail always renders the full window — there is no
  // sub-selection (time-dock Phase 2). Drives the kept-imperative TimeStrip via
  // setData/actions and the map via the passed MapView façade (`view` is a prop so
  // this component never imports map.ts — that would pull MapLibre into the main
  // bundle). It also renders the annotation overlays (pins/range bands + strip
  // ticks) against the loaded points.
  let { view }: { view?: typeof MapViewType } = $props()

  let canvas: HTMLCanvasElement
  let tooltip: HTMLElement
  let strip: TimeStripHandle | undefined

  let allPoints = $state<TrackPoint[]>([])
  let status = $state('')
  let empty = $state('')
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
    // Live re-emits (anchor sliding forward) are continuations — don't refit each tick.
    const isLiveTick =
      !!lastRange &&
      lastRange.live &&
      live &&
      lastRange.mode === range.mode &&
      lastRange.windowMs === range.windowMs
    lastRange = range
    startLabel = windowLabel(from.getTime())
    endLabel = windowLabel(to.getTime())
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
    // The axis is the requested window, not the data extent (S1) — and it always
    // renders, even with zero points: an empty-density strip is still navigable
    // and its annotation bands stay reachable.
    strip?.setData({ startMs: from.getTime(), endMs: to.getTime(), points: allPoints })
    if (!allPoints.length) {
      empty = 'No GPS points for this window'
      view.clearTrack()
      return
    }
    view.showTrack(allPoints, { fitBounds: !isLiveTick })
    // A point annotation was just clicked: recentre on its nearest fix now that
    // the reframed window's points have landed.
    if (annotations.pendingPan) {
      const nearest = nearestByTime(allPoints, annotations.pendingPan)
      annotations.pendingPan = null
      if (nearest) view.zoomTo(nearest.lat, nearest.lon, 14)
    }
  }

  // Name the current window — a range annotation is a saved window (time-dock plan).
  function saveWindow(): void {
    const r = selection.range
    annotations.openCreate(r.from.toISOString(), r.to.toISOString())
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
    strip = mountTimeStrip(canvas, tooltip, {
      onZoom: (loMs, hiMs) => selection.zoomTo(new Date(loMs), new Date(hiMs)),
      onWiden: () => selection.widen(),
      onShift: (dir) => selection.shift(dir),
      onBack: () => selection.back(),
    })
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
    <div class="tl-nav">
      <button type="button" title="Back one window (←)" onclick={() => selection.shift(-1)}>◀</button>
      <button type="button" title="Widen ×2 (−)" onclick={() => selection.widen()}>⊖</button>
      <button
        type="button"
        title="Forward one window (→)"
        disabled={selection.live}
        onclick={() => selection.shift(1)}
      >▶</button>
      {#if selection.canGoBack}
        <button type="button" title="Back to the previous window (Backspace)" onclick={() => selection.back()}>↩</button>
      {/if}
      {#if selection.live}<span class="tl-live">LIVE</span>{/if}
    </div>
    <span class="tl-status">{status}</span>
  </div>

  <div class="tl-strip-wrap">
    <div class="tl-strip-canvas-wrap">
      <canvas class="tl-strip" bind:this={canvas}></canvas>
      <div class="tl-strip-tooltip hidden" bind:this={tooltip}></div>
    </div>
    <div class="tl-slider-labels">
      <span>{startLabel}</span>
      <span class="muted">{empty || 'drag to zoom · double-click to go back'}</span>
      <span>{endLabel}</span>
    </div>
  </div>

  <div class="tl-bottom-actions">
    {#if selection.live}
      <button class="btn-secondary" disabled={bookmarked} onclick={bookmarkHere}>
        {bookmarked ? '✓ Bookmarked' : '📍 Bookmark Here'}
      </button>
    {/if}
    <button class="btn-secondary" onclick={saveWindow}>💾 Save window</button>
  </div>
</div>
