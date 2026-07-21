<script lang="ts">
  /**
   * Presentational waveform bar strip. The whole envelope draws as one SVG
   * `<path>` (cheap DOM even at hundreds of bars, and vector so it stays crisp at
   * any width and recolors with the theme); a second path over the played prefix
   * paints it in the accent color. Non-interactive — the player overlays the
   * playhead and owns click-to-seek. Reused by the collapsed log row (progress 0).
   */
  import { barsPath } from './radio'

  interface Props {
    /** Normalized bar heights, 0..1 (see `parseWaveform`). */
    samples: number[]
    /** Played fraction 0..1; the accent prefix is drawn up to it. */
    progress?: number
    /** Rendered pixel height of the strip. */
    height?: number
  }
  let { samples, progress = 0, height = 28 }: Props = $props()

  const n = $derived(samples.length)
  const base = $derived(barsPath(samples))
  const played = $derived(barsPath(samples.slice(0, Math.round(progress * n))))
</script>

{#if n > 0}
  <svg
    class="wf"
    viewBox="0 0 {n} 100"
    preserveAspectRatio="none"
    style="height:{height}px"
    aria-hidden="true"
  >
    <path class="wf-base" d={base} />
    {#if played}
      <path class="wf-played" d={played} />
    {/if}
  </svg>
{/if}

<style>
  .wf {
    display: block;
    width: 100%;
  }
  .wf-base {
    fill: var(--text-dim);
    opacity: 0.5;
  }
  .wf-played {
    fill: var(--accent);
  }
</style>
