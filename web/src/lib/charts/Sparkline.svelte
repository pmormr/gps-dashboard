<script lang="ts">
  // A compact inline-SVG sparkline — deliberately NOT the LayerCake Trend chart:
  // a Sensors card can hold dozens of these, so each is one cheap <path>, no d3
  // instance. Full-resolution graphing is the Trends view (tap a metric to open it).
  import { sparklinePath } from './util'

  let {
    values,
    color = 'var(--accent)',
    width = 96,
    height = 22,
  }: { values: (number | null)[]; color?: string; width?: number; height?: number } = $props()

  const d = $derived(sparklinePath(values, width, height))
</script>

{#if d}
  <svg class="spark" viewBox="0 0 {width} {height}" preserveAspectRatio="none" aria-hidden="true">
    <path
      {d}
      fill="none"
      stroke={color}
      stroke-width="1.5"
      stroke-linejoin="round"
      vector-effect="non-scaling-stroke"
    />
  </svg>
{:else}
  <div class="spark-empty" aria-hidden="true"></div>
{/if}

<style>
  .spark {
    display: block;
    width: 100%;
    height: 22px;
    margin-top: 5px;
    overflow: visible;
  }
  /* Reserve the same vertical space so a card without recent data doesn't jump. */
  .spark-empty {
    height: 22px;
    margin-top: 5px;
  }
</style>
