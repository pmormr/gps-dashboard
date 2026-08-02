<script lang="ts">
  import { onDestroy, onMount } from 'svelte'

  import CamTile from '../lib/CamTile.svelte'
  import { getCameras, type Camera } from '../lib/api'
  import { drivingCameras } from '../lib/cameras'
  import { errMsg } from '../lib/errors'
  import { acquireWakeLock, releaseWakeLock } from '../lib/wakelock'

  // Driving mode: a pre-warmed wall of the blind-spot + rear feeds, all live at
  // once (each CamTile opens its own WHEP session on mount — the C4 pre-warm, so
  // the on-demand connect latency is paid up front, not mid-drive). Tiles default
  // to the D1 sub feed to keep three simultaneous decodes light on a phone; the
  // HD toggle bumps them to 720p (`{#key hd}` remounts the wall to switch cleanly).
  let cams = $state<Camera[]>([])
  let loadError = $state('')
  let hd = $state(false)

  onMount(() => {
    getCameras()
      .then((r) => (cams = drivingCameras(r.cameras)))
      .catch((e) => (loadError = errMsg(e)))
    void acquireWakeLock() // held the whole time the wall is open, not just per-feed
  })
  onDestroy(() => releaseWakeLock())
</script>

<div class="drive-cam">
  <div class="bar">
    <span class="title">Driving view</span>
    <button class="quality" onclick={() => (hd = !hd)} aria-pressed={hd}>
      {hd ? 'HD 720p' : 'SD D1'}
    </button>
  </div>

  {#if loadError}
    <p class="load-error">Couldn't load the cameras: {loadError}</p>
  {:else if cams.length === 0}
    <p class="empty">No driving cameras configured.</p>
  {:else}
    {#key hd}
      <div class="wall" style={`--cols:${cams.length}`}>
        {#each cams as cam (cam.node)}
          <CamTile {cam} {hd} />
        {/each}
      </div>
    {/key}
  {/if}
</div>

<style>
  .drive-cam {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    padding: 0 0.5rem 0.5rem;
    gap: 0.5rem;
  }
  .bar {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.4rem 0.25rem 0;
  }
  .title {
    font-weight: 700;
    color: var(--text);
  }
  .quality {
    margin-left: auto;
    padding: 0.35rem 0.8rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    color: var(--text);
    font-weight: 600;
    cursor: pointer;
  }
  .quality[aria-pressed='true'] {
    border-color: var(--accent, var(--text));
    color: var(--accent, var(--text));
  }

  /* Portrait (phone on a mount): stack the feeds, each filling an equal slice of
     the height so all are visible without scrolling. Landscape: a row instead. */
  .wall {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-rows: repeat(var(--cols), 1fr);
    gap: 0.5rem;
  }
  @media (orientation: landscape) {
    .wall {
      grid-template-rows: none;
      grid-template-columns: repeat(var(--cols), 1fr);
    }
  }

  .load-error {
    color: var(--err);
    padding: 0 0.25rem;
  }
  .empty {
    color: var(--text-dim);
    padding: 0 0.25rem;
  }
</style>
