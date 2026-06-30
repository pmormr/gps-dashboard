<script lang="ts">
  // Bottom time axis: faint vertical gridlines + local-time tick labels, from the
  // LayerCake-provided x (time) scale.
  import type { ScaleTime } from 'd3-scale'
  import { getContext } from 'svelte'
  import type { Readable } from 'svelte/store'

  interface LC {
    height: Readable<number>
    xScale: Readable<ScaleTime<number, number>>
  }
  const { height, xScale } = getContext<LC>('LayerCake')

  let { ticks = 6 }: { ticks?: number } = $props()

  /** Compact, adaptive: a date at midnight (day boundaries / long windows), else time. */
  function fmt(d: Date): string {
    if (d.getHours() === 0 && d.getMinutes() === 0) {
      return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
    }
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  }
</script>

{#each $xScale.ticks(ticks) as t (t.valueOf())}
  {@const x = $xScale(t)}
  <g transform="translate({x} 0)">
    <line y1="0" y2={$height} class="grid" />
    <text y={$height + 14} text-anchor="middle" class="lbl">{fmt(t)}</text>
  </g>
{/each}

<style>
  .grid {
    stroke: #1e293b;
    stroke-width: 1;
  }
  .lbl {
    fill: #94a3b8;
    font-size: 10px;
  }
</style>
