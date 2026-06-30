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
  import { axisForUnits, extent, movingAverage, padDomain, unionExtent } from './util'

  let {
    resp,
    smoothWindow = 1,
    showBand = false,
    hidden = new Set<string>(),
    height = 300,
  }: {
    resp: SensorSeriesResponse | null
    smoothWindow?: number
    showBand?: boolean
    hidden?: Set<string>
    height?: number
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

  function onMove(e: PointerEvent): void {
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
  role="img"
  aria-label="Trend chart"
  style:height="{height}px"
  bind:clientWidth={cw}
  onpointermove={onMove}
  onpointerleave={() => (hoverIdx = null)}
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

    {#if hoverIdx != null}
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
  .crosshair {
    position: absolute;
    width: 1px;
    background: #64748b;
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
