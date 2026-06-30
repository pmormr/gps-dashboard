<script lang="ts">
  // A value axis (left or right). Builds its own linear scale from the passed
  // domain + plot height, draws horizontal gridlines + tick labels, and tags the
  // axis with its unit in the series color.
  import { scaleLinear } from 'd3-scale'
  import { getContext } from 'svelte'
  import type { Readable } from 'svelte/store'

  interface LC {
    width: Readable<number>
    height: Readable<number>
  }
  const { width, height } = getContext<LC>('LayerCake')

  let {
    domain,
    side = 'left',
    unit = '',
    color = '#94a3b8',
    ticks = 5,
  }: {
    domain: [number, number]
    side?: 'left' | 'right'
    unit?: string
    color?: string
    ticks?: number
  } = $props()

  const scale = $derived(scaleLinear(domain, [$height, 0]))
</script>

{#each scale.ticks(ticks) as tick (tick)}
  <g transform="translate(0 {scale(tick)})">
    <line x1="0" x2={$width} class="grid" />
    {#if side === 'left'}
      <text x="-6" dy="0.32em" text-anchor="end" class="lbl">{tick}</text>
    {:else}
      <text x={$width + 6} dy="0.32em" text-anchor="start" class="lbl">{tick}</text>
    {/if}
  </g>
{/each}
{#if unit}
  <text
    class="unit"
    style="fill:{color}"
    x={side === 'left' ? -6 : $width + 6}
    y="-4"
    text-anchor={side === 'left' ? 'end' : 'start'}>{unit}</text
  >
{/if}

<style>
  .grid {
    stroke: #1e293b;
    stroke-width: 1;
  }
  .lbl {
    fill: #94a3b8;
    font-size: 10px;
  }
  .unit {
    font-size: 10px;
    font-weight: 600;
  }
</style>
