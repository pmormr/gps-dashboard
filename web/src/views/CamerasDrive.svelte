<script lang="ts">
  import { onDestroy, onMount } from 'svelte'

  import CamTile from '../lib/CamTile.svelte'
  import { getCameras, type Camera } from '../lib/api'
  import {
    type CamAlign,
    clearAlign,
    defaultAlign,
    drivingCameras,
    mergedAlign,
    saveAlign,
  } from '../lib/cameras'
  import { errMsg } from '../lib/errors'
  import { acquireWakeLock, releaseWakeLock } from '../lib/wakelock'

  // Driving mode: the blind-spot + rear feeds butt edge-to-edge into one seamless
  // 180° strip. Each tile pre-warms its own WHEP session on mount (C4 — pay the
  // on-demand connect cost up front, not mid-drive) and auto-reconnects on a drop.
  // The strip is locked to the 720p `-hd` stream: the D1 sub is a different aspect
  // ratio (704×480 vs 1280×720), so under object-fit:cover the same alignment frames
  // the two differently — one stream keeps the crop stable (and matches the 16:9
  // stills the defaults were fitted to). Align mode reveals per-camera crop/pan/zoom/
  // width controls tuned live against the feeds (the geometry is fixed by mounting,
  // so parked tuning carries to the road) and persisted per-device.
  let cams = $state<Camera[]>([])
  let loadError = $state('')
  let aligning = $state(false)
  let sel = $state('') // node currently being tuned
  let align = $state<Record<string, CamAlign>>({})
  let copied = $state(false)

  onMount(() => {
    getCameras()
      .then((r) => {
        cams = drivingCameras(r.cameras)
        const nodes = cams.map((c) => c.node)
        align = mergedAlign(nodes)
        sel = nodes.find((n) => n === 'van-cam-rear') ?? nodes[0] ?? ''
      })
      .catch((e) => (loadError = errMsg(e)))
    void acquireWakeLock() // held the whole time the wall is open, not just per-feed
  })
  onDestroy(() => releaseWakeLock())

  const cur = $derived(align[sel])

  function persist(): void {
    saveAlign(align)
  }
  function resetAlign(): void {
    align = Object.fromEntries(cams.map((c) => [c.node, { ...defaultAlign(c.node) }]))
    clearAlign() // drop the stored override so a reload is pristine too
  }
  async function copyValues(): Promise<void> {
    try {
      await navigator.clipboard?.writeText(JSON.stringify($state.snapshot(align), null, 2))
      copied = true
      setTimeout(() => (copied = false), 1500)
    } catch {
      // clipboard blocked — the values are still live on-screen
    }
  }
</script>

<div class="drive-cam">
  <div class="bar">
    <span class="title">Driving view</span>
    <button class="btn" class:on={aligning} onclick={() => (aligning = !aligning)} aria-pressed={aligning}>
      Align
    </button>
  </div>

  {#if loadError}
    <p class="load-error">Couldn't load the cameras: {loadError}</p>
  {:else if cams.length === 0}
    <p class="empty">No driving cameras configured.</p>
  {:else}
    <div class="wall" class:aligning>
      {#each cams as cam (cam.node)}
        <CamTile {cam} hd={true} align={align[cam.node]} />
      {/each}
    </div>
  {/if}

  {#if aligning && cur}
    <div class="align">
      <div class="picker">
        {#each cams as c (c.node)}
          <button class="chip" class:active={c.node === sel} onclick={() => (sel = c.node)}>
            {c.label}
          </button>
        {/each}
      </div>
      <div class="sliders">
        <label>
          <span>Zoom</span>
          <input type="range" min="1" max="2.5" step="0.01" bind:value={cur.scale} oninput={persist} />
        </label>
        <label>
          <span>Pan X</span>
          <input type="range" min="-0.6" max="0.6" step="0.005" bind:value={cur.panX} oninput={persist} />
        </label>
        <label>
          <span>Pan Y</span>
          <input type="range" min="-0.6" max="0.6" step="0.005" bind:value={cur.panY} oninput={persist} />
        </label>
        <label>
          <span>Width</span>
          <input type="range" min="0.5" max="2.5" step="0.05" bind:value={cur.weight} oninput={persist} />
        </label>
        <label class="flip">
          <input type="checkbox" bind:checked={cur.flip} onchange={persist} />
          <span>Flip</span>
        </label>
      </div>
      <div class="actions">
        <button class="btn" onclick={resetAlign}>Reset</button>
        <button class="btn" onclick={copyValues}>{copied ? 'Copied ✓' : 'Copy values'}</button>
      </div>
    </div>
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
    gap: 0.5rem;
    padding: 0.4rem 0.25rem 0;
  }
  .title {
    font-weight: 700;
    color: var(--text);
    margin-right: auto;
  }
  .btn {
    padding: 0.35rem 0.8rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    color: var(--text);
    font-weight: 600;
    cursor: pointer;
  }
  .btn.on,
  .btn[aria-pressed='true'] {
    border-color: var(--accent, var(--text));
    color: var(--accent, var(--text));
  }

  /* The 180° strip: feeds butt edge-to-edge (no gap, no rounding) into one band.
     A panorama is inherently horizontal, so it's always a row; each feed's width
     is its `weight`. In Align mode a hairline marks each seam. */
  .wall {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: row;
    gap: 0;
    background: #000;
    border-radius: 8px;
    overflow: hidden;
  }
  .wall.aligning :global(.cell + .cell) {
    box-shadow: inset 1px 0 0 rgba(255, 255, 255, 0.5);
  }

  .align {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    padding: 0.5rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
  }
  .picker {
    display: flex;
    gap: 0.4rem;
  }
  .chip {
    flex: 1;
    padding: 0.35rem 0.5rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg);
    color: var(--text);
    font-weight: 600;
    cursor: pointer;
  }
  .chip.active {
    border-color: var(--accent, var(--text));
    color: var(--accent, var(--text));
  }
  .sliders {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 0.5rem 0.9rem;
  }
  .sliders label {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.85rem;
    color: var(--text-dim);
  }
  .sliders label span {
    width: 3.2rem;
    flex: none;
  }
  .sliders input[type='range'] {
    flex: 1;
    min-width: 0;
  }
  .sliders label.flip {
    gap: 0.4rem;
  }
  .actions {
    display: flex;
    gap: 0.5rem;
    justify-content: flex-end;
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
