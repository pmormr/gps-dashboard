<script lang="ts">
  // The trend plot: a LayerCake-composed multi-line chart over a shared x grid.
  // Owns smoothing, unit→axis assignment (left/right, Phase-1 two-axis cap), and a
  // shared-grid hover read-out. Layers (Line/AxisX/AxisY) read context; the
  // crosshair/tooltip live here as absolute overlays so LayerCake stays pointer-free.
  import { scaleTime } from 'd3-scale'
  import { LayerCake, Svg } from 'layercake'

  import type { SensorSeriesResponse } from '../api'
  import AxisX from './AxisX.svelte'
  import AxisY from './AxisY.svelte'
  import Band from './Band.svelte'
  import Line from './Line.svelte'
  import { axisForUnits, extent, movingAverage, padDomain, pixelToTime, unionExtent } from './util'

  let {
    resp,
    smoothWindow = 1,
    showBand = false,
    hidden = new Set<string>(),
    height = 300,
    onzoom,
    onresetzoom,
  }: {
    resp: SensorSeriesResponse | null
    smoothWindow?: number
    showBand?: boolean
    hidden?: Set<string>
    height?: number
    /** Drag-select a region → zoom to `[fromMs, hiMs]`. The parent owns the window. */
    onzoom?: (fromMs: number, toMs: number) => void
    /** Double-click → reset zoom (the parent decides what "reset" means). */
    onresetzoom?: () => void
  } = $props()

  interface Prepared {
    metric: string
    label: string
    unit: string
    color: string
    dec: number
    values: (number | null)[]
    min: (number | null)[]
    max: (number | null)[]
  }

  const prepared = $derived(
    (resp?.series ?? [])
      .filter((s) => !hidden.has(s.metric))
      .map((s) => ({
        metric: s.metric,
        label: s.label,
        unit: s.unit,
        color: s.color,
        dec: s.dec,
        // The global control is a floor the per-metric default can raise (noisy
        // channels like fuel smooth even at Off) but not lower.
        values: movingAverage(s.values, Math.max(smoothWindow, s.smooth)),
        min: s.min, // band: raw spread, never smoothed (that would hide spikes)
        max: s.max,
      }))
  )

  const sides = $derived(axisForUnits(prepared.map((s) => s.unit)))
  const leftSeries = $derived(prepared.filter((s) => sides.get(s.unit) === 'left'))
  const rightSeries = $derived(prepared.filter((s) => sides.get(s.unit) === 'right'))
  const leftDomain = $derived(domainFor(leftSeries))
  const rightDomain = $derived(domainFor(rightSeries))

  /** Axis domain: the band's raw spread when shown (so excursions fit), else the line. */
  function domainFor(series: Prepared[]): [number, number] | null {
    const extents = series.flatMap((s) =>
      showBand ? [extent(s.values), extent(s.min), extent(s.max)] : [extent(s.values)]
    )
    const ex = unionExtent(extents)
    return ex ? padDomain(ex) : null
  }

  const x = $derived(resp?.x ?? [])
  const gridRows = $derived(x.map((t) => ({ t })))
  const startMs = $derived(resp ? Date.parse(resp.start) : 0)
  const endMs = $derived(resp ? Date.parse(resp.end) : 0)
  const padding = $derived({ top: 12, right: rightDomain ? 46 : 14, bottom: 22, left: 44 })

  function pointsFor(s: Prepared): { t: number; v: number | null }[] {
    return x.map((t, i) => ({ t, v: s.values[i] }))
  }
  function bandPointsFor(s: Prepared): { t: number; lo: number | null; hi: number | null }[] {
    return x.map((t, i) => ({ t, lo: s.min[i], hi: s.max[i] }))
  }
  function domainOf(s: Prepared): [number, number] {
    return (sides.get(s.unit) === 'right' ? rightDomain : leftDomain) ?? [0, 1]
  }

  // ── Hover read-out (shared-grid nearest-index, no per-series hit testing) ──
  let cw = $state(0)
  let hoverIdx = $state<number | null>(null)
  const n = $derived(x.length)
  const plotW = $derived(Math.max(1, cw - padding.left - padding.right))
  const hoverX = $derived(
    hoverIdx == null || n < 2 ? 0 : padding.left + (hoverIdx / (n - 1)) * plotW
  )

  // ── Drag-to-zoom: select a region → the parent narrows the window (true zoom:
  // a narrower window re-buckets at finer resolution, so sparse bursts spread out).
  const DRAG_THRESHOLD = 6 // px; below this a press is a click, not a region select
  let dragStartX = $state<number | null>(null)
  let dragCurX = $state<number | null>(null)
  const dragging = $derived(dragStartX != null && dragCurX != null)
  const dragLo = $derived(dragging ? Math.min(dragStartX!, dragCurX!) : 0)
  const dragHi = $derived(dragging ? Math.max(dragStartX!, dragCurX!) : 0)

  /** Clamp a clientX (relative to the element) to the plot's x band. */
  function plotX(e: PointerEvent): number {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
    return Math.min(cw - padding.right, Math.max(padding.left, e.clientX - rect.left))
  }

  function onDown(e: PointerEvent): void {
    if (e.button !== 0 || n < 2 || !onzoom) return
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    dragStartX = dragCurX = plotX(e)
    hoverIdx = null
  }

  function onMove(e: PointerEvent): void {
    if (dragging) {
      dragCurX = plotX(e)
      return
    }
    if (n < 2) return
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
    const mx = e.clientX - rect.left
    if (mx < padding.left || mx > cw - padding.right) {
      hoverIdx = null
      return
    }
    const frac = (mx - padding.left) / plotW
    hoverIdx = Math.min(n - 1, Math.max(0, Math.round(frac * (n - 1))))
  }

  function onUp(e: PointerEvent): void {
    if (!dragging) return
    const lo = dragLo
    const hi = dragHi
    dragStartX = dragCurX = null
    if (hi - lo < DRAG_THRESHOLD || !onzoom) return
    const fromMs = pixelToTime(lo, padding.left, plotW, startMs, endMs)
    const toMs = pixelToTime(hi, padding.left, plotW, startMs, endMs)
    if (toMs > fromMs) onzoom(fromMs, toMs)
  }

  function hoverTime(): string {
    if (hoverIdx == null) return ''
    return new Date(x[hoverIdx]).toLocaleString([], {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }
  const flip = $derived(hoverX > cw * 0.62)
</script>

<div
  class="chart"
  class:zoomable={!!onzoom}
  role="img"
  aria-label="Trend chart"
  style:height="{height}px"
  bind:clientWidth={cw}
  onpointerdown={onDown}
  onpointermove={onMove}
  onpointerup={onUp}
  onpointerleave={() => {
    hoverIdx = null
    dragStartX = dragCurX = null
  }}
  ondblclick={() => onresetzoom?.()}
>
  {#if resp && prepared.length}
    <LayerCake
      data={gridRows}
      x={(d: { t: number }) => d.t}
      xScale={scaleTime()}
      xDomain={[startMs, endMs]}
      {padding}
      pointerEvents={false}
    >
      <Svg>
        <AxisX />
        {#if leftDomain}
          <AxisY domain={leftDomain} side="left" unit={leftSeries[0]?.unit} color={leftSeries[0]?.color} />
        {/if}
        {#if rightDomain}
          <AxisY domain={rightDomain} side="right" unit={rightSeries[0]?.unit} color={rightSeries[0]?.color} />
        {/if}
        {#if showBand}
          {#each prepared as s (s.metric)}
            <Band points={bandPointsFor(s)} domain={domainOf(s)} color={s.color} />
          {/each}
        {/if}
        {#each prepared as s (s.metric)}
          <Line points={pointsFor(s)} domain={domainOf(s)} color={s.color} />
        {/each}
      </Svg>
    </LayerCake>

    {#if dragging}
      <div
        class="drag-sel"
        style:left="{dragLo}px"
        style:width="{dragHi - dragLo}px"
        style:top="{padding.top}px"
        style:height="{height - padding.top - padding.bottom}px"
      ></div>
    {/if}

    {#if hoverIdx != null && !dragging}
      {@const hi = hoverIdx}
      <div class="crosshair" style:left="{hoverX}px" style:top="{padding.top}px" style:height="{height - padding.top - padding.bottom}px"></div>
      <div class="tip" class:flip style:left="{hoverX}px" style:top="{padding.top}px">
        <div class="tip-time">{hoverTime()}</div>
        {#each prepared as s (s.metric)}
          {@const v = s.values[hi]}
          <div class="tip-row">
            <span class="sw" style:background={s.color}></span>
            <span class="tip-lbl">{s.label}</span>
            <span class="tip-val">{v == null ? '—' : v.toFixed(s.dec)}{s.unit}</span>
          </div>
        {/each}
      </div>
    {/if}
  {:else}
    <div class="empty">{resp ? 'Pick a metric to plot.' : 'Loading…'}</div>
  {/if}
</div>

<style>
  .chart {
    position: relative;
    width: 100%;
    touch-action: none;
  }
  .empty {
    display: grid;
    place-items: center;
    height: 100%;
    color: var(--text-dim);
    font-size: 13px;
  }
  .chart.zoomable {
    cursor: crosshair;
  }
  .crosshair {
    position: absolute;
    width: 1px;
    background: #64748b;
    pointer-events: none;
  }
  .drag-sel {
    position: absolute;
    background: color-mix(in srgb, var(--accent) 18%, transparent);
    border-left: 1px solid var(--accent);
    border-right: 1px solid var(--accent);
    pointer-events: none;
  }
  .tip {
    position: absolute;
    transform: translate(10px, 0);
    min-width: 120px;
    padding: 6px 8px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    font-size: 11px;
    pointer-events: none;
    z-index: 2;
  }
  .tip.flip {
    transform: translate(calc(-100% - 10px), 0);
  }
  .tip-time {
    color: var(--text-dim);
    margin-bottom: 4px;
  }
  .tip-row {
    display: flex;
    align-items: center;
    gap: 6px;
    white-space: nowrap;
  }
  .sw {
    width: 8px;
    height: 8px;
    border-radius: 2px;
    flex-shrink: 0;
  }
  .tip-lbl {
    color: var(--text-dim);
  }
  .tip-val {
    margin-left: auto;
    font-variant-numeric: tabular-nums;
  }
</style>
