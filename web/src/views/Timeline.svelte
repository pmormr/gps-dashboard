<script lang="ts">
  import { onMount } from 'svelte'

  import { getPoints } from '../lib/api'
  import type { TrackPoint } from '../lib/geo'
  import type { MapView as MapViewType } from '../lib/map'
  import { selection, type Range } from '../lib/stores/selection.svelte'
  import { mountTimeStrip, type TimeStripHandle } from '../lib/timestrip'
  import './timeline.css'
  import TimePicker from './TimePicker.svelte'

  // The Selection-axis chrome (ported from timeline.js): the window picker, the
  // TimeStrip brush, and the trail render for the selection. Drives the
  // kept-imperative TimeStrip via setData/getSelection/onBrush and the map via the
  // passed MapView façade (`view` is a prop so this component never imports map.ts
  // — that would pull MapLibre into the main bundle).
  let { view }: { view?: typeof MapViewType } = $props()

  let canvas: HTMLCanvasElement
  let tooltip: HTMLElement
  let strip: TimeStripHandle | undefined

  let allPoints: TrackPoint[] = []
  let status = $state('')
  let empty = $state('')
  let hasData = $state(false)
  let selCount = $state(0)
  let canZoom = $state(false)
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

  // Render the current brush: trail/map, the count, the Zoom-to-Range gating, and
  // the store's brush (for other axis consumers). followMap fits on initial load.
  function renderRange(followMap: boolean): void {
    const sel = strip?.getSelection()
    if (!sel || !view) return
    selection.setBrush(sel.loMs, sel.hiMs)
    startLabel = windowLabel(sel.loMs)
    endLabel = windowLabel(sel.hiMs)
    const pts = pointsInRange(sel.loMs, sel.hiMs)
    const isSub = selection.isSubRange
    view.showTrack(pts, { fitBounds: followMap, showEndpoints: isSub && pts.length > 1 })
    selCount = pts.length
    canZoom = isSub
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
      return
    }
    hasData = true
    // The axis is the requested window, not the data extent (S1): empty leading/
    // trailing time stays visible; data renders onto it via the TimeStrip canvas.
    selection.setLoaded(from.getTime(), to.getTime())
    strip?.setData({ startMs: from.getTime(), endMs: to.getTime(), points: allPoints })
    renderRange(!isLiveTick)
  }

  // Narrow the loaded window to the brush and re-fetch — same vertex budget over a
  // smaller window yields more detail.
  function zoomToRange(): void {
    const sel = strip?.getSelection()
    if (!sel || sel.hiMs <= sel.loMs) return
    selection.setRange(new Date(sel.loMs), new Date(sel.hiMs))
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
  </div>
</div>
