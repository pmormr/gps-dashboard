<script lang="ts">
  /**
   * Interactive transmission player: the waveform strip is the scrub surface
   * (click-to-seek + a playhead synced to the `<audio>` currentTime), with the
   * native audio controls kept below for play/pause/volume. `durationS` comes
   * from the row (authoritative and available before audio metadata loads), so
   * the geometry is stable from first paint.
   */
  import { radioAudioUrl } from './api'
  import { cursorX, seekTime } from './radio'
  import WaveformStrip from './WaveformStrip.svelte'

  interface Props {
    id: number
    durationS: number
    samples: number[]
  }
  let { id, durationS, samples }: Props = $props()

  let audio = $state<HTMLAudioElement>()
  let currentTime = $state(0)
  let width = $state(0)

  const progress = $derived(durationS > 0 ? currentTime / durationS : 0)
  const cursorLeft = $derived(cursorX(currentTime, durationS, width))

  function seek(e: MouseEvent): void {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
    if (audio) audio.currentTime = seekTime(e.clientX - rect.left, rect.width, durationS)
  }
</script>

<div class="wf-player">
  <button type="button" class="wf-seek" bind:clientWidth={width} onclick={seek} title="Click to seek">
    <WaveformStrip {samples} {progress} height={48} />
    {#if samples.length}
      <div class="wf-cursor" style="left:{cursorLeft}px"></div>
    {/if}
  </button>
  <!-- svelte-ignore a11y_media_has_caption -->
  <audio
    bind:this={audio}
    bind:currentTime
    controls
    autoplay
    preload="metadata"
    src={radioAudioUrl(id)}
  ></audio>
</div>

<style>
  .wf-player {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .wf-seek {
    display: block;
    width: 100%;
    position: relative;
    cursor: pointer;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 5px 0;
    font: inherit;
    color: inherit;
  }
  .wf-cursor {
    position: absolute;
    top: 3px;
    bottom: 3px;
    width: 2px;
    margin-left: -1px;
    background: var(--accent);
    pointer-events: none;
  }
  .wf-player audio {
    width: 100%;
    height: 40px;
  }
</style>
