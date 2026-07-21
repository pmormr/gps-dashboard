<script lang="ts">
  /**
   * Presentational waveform bar strip. Absolute-encoded bar heights (0..1) drawn
   * as centered SVG bars; bars left of `progress` render in the accent color
   * (played), the rest dimmed. Non-interactive — the player overlays the playhead
   * and owns click-to-seek. Reused by the collapsed log row (progress 0, static).
   */
  interface Props {
    /** Normalized bar heights, 0..1 (see `parseWaveform`). */
    samples: number[]
    /** Played fraction 0..1; splits bar color at the playhead. */
    progress?: number
    /** Rendered pixel height of the strip. */
    height?: number
  }
  let { samples, progress = 0, height = 28 }: Props = $props()

  const n = $derived(samples.length)
  const playedBars = $derived(Math.round(progress * n))
  const barHeight = (s: number): number => Math.max(s * 100, 3)
</script>

{#if n > 0}
  <svg
    class="wf"
    viewBox="0 0 {n} 100"
    preserveAspectRatio="none"
    style="height:{height}px"
    aria-hidden="true"
  >
    {#each samples as s, i (i)}
      <rect
        class="bar"
        class:played={i < playedBars}
        x={i + 0.12}
        width="0.76"
        y={(100 - barHeight(s)) / 2}
        height={barHeight(s)}
      />
    {/each}
  </svg>
{/if}

<style>
  .wf {
    display: block;
    width: 100%;
  }
  .bar {
    fill: var(--text-dim);
    opacity: 0.5;
  }
  .bar.played {
    fill: var(--accent);
    opacity: 1;
  }
</style>
