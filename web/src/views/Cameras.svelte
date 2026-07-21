<script lang="ts">
  import { onDestroy, onMount } from 'svelte'

  import { getCameras, type Camera } from '../lib/api'
  import { SNAPSHOT_REFRESH_MS, livePath, snapshotUrl } from '../lib/cameras'
  import { errMsg } from '../lib/errors'
  import { acquireWakeLock, releaseWakeLock } from '../lib/wakelock'
  import { startWhep, whepEndpoint, type WhepSession } from '../lib/whep'

  // Glance-first grid: each tile is a JPEG still refreshed on a shared cache-bust
  // stamp (cheap, HaLow-friendly). Tapping a tile opens the live 720p WHEP feed.
  let cameras = $state<Camera[]>([])
  let loadError = $state('')
  let bust = $state(1) // shared cache-buster; bumping it refetches every tile
  let offline = $state<Record<string, boolean>>({}) // node → last snapshot failed

  // Live overlay (tap a tile → 720p WHEP video over the Phase 2 client).
  let liveCam = $state<Camera | null>(null)
  let liveState = $state<'connecting' | 'live' | 'error'>('connecting')
  let liveErr = $state('')
  let videoEl = $state<HTMLVideoElement>()
  let session: WhepSession | null = null

  function tick(): void {
    // Pause polling while watching a feed (grid hidden) or backgrounded.
    if (liveCam || document.hidden) return
    bust += 1
  }

  onMount(() => {
    getCameras()
      .then((r) => (cameras = r.cameras))
      .catch((e) => (loadError = errMsg(e)))
    const timer = window.setInterval(tick, SNAPSHOT_REFRESH_MS)
    return () => clearInterval(timer)
  })
  onDestroy(() => closeLive())

  function onImgError(node: string): void {
    if (!offline[node]) offline = { ...offline, [node]: true }
  }
  function onImgLoad(node: string): void {
    if (offline[node]) offline = { ...offline, [node]: false }
  }

  function onLiveDropped(): void {
    // Fired only when the peer drops on its own — a user close resets state first.
    if (liveState === 'live') {
      liveState = 'error'
      liveErr = 'Stream dropped'
    }
  }

  async function openLive(cam: Camera): Promise<void> {
    liveCam = cam
    liveState = 'connecting'
    liveErr = ''
    await acquireWakeLock()
    try {
      session = await startWhep(whepEndpoint(livePath(cam, true)), {
        media: ['video'],
        onClosed: onLiveDropped,
        unreachableMessage: 'Could not reach the camera hub — is mediamtx running?',
      })
      if (videoEl) {
        videoEl.srcObject = session.stream
        await videoEl.play().catch(() => {}) // click gesture allows playback; ignore edge rejections
      }
      liveState = 'live'
    } catch (e) {
      liveErr = errMsg(e)
      liveState = 'error'
    }
  }

  function closeLive(): void {
    session?.close()
    session = null
    if (videoEl) videoEl.srcObject = null
    liveCam = null
    releaseWakeLock()
  }
</script>

<header class="page-head">
  <h1>Cameras</h1>
  <p class="muted">Live view of the van's cameras · tap a tile to expand</p>
</header>

{#if loadError}
  <p class="load-error">Couldn't load the camera list: {loadError}</p>
{/if}

<div class="grid">
  {#each cameras as cam (cam.node)}
    <button class="tile" onclick={() => openLive(cam)} aria-label={`Expand ${cam.label}`}>
      {#if offline[cam.node]}
        <div class="tile-offline">No image</div>
      {:else}
        <img src={snapshotUrl(cam.node, bust)} alt={cam.label} onerror={() => onImgError(cam.node)} onload={() => onImgLoad(cam.node)} />
      {/if}
      <span class="tile-label">{cam.label}</span>
    </button>
  {/each}
</div>

{#if liveCam}
  <div class="live" role="dialog" aria-label={`${liveCam.label} live`}>
    <!-- svelte-ignore a11y_media_has_caption -->
    <video bind:this={videoEl} autoplay playsinline muted></video>
    <div class="live-bar">
      <span class="live-title">{liveCam.label}</span>
      {#if liveState === 'connecting'}<span class="live-note">Connecting…</span>{/if}
      {#if liveState === 'error'}<span class="live-note err">{liveErr}</span>{/if}
      <button class="live-close" onclick={closeLive}>Close</button>
    </div>
  </div>
{/if}

<style>
  .grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 0.5rem;
    padding: 0 0.75rem 0.75rem;
  }
  @media (min-width: 900px) {
    .grid {
      max-width: 1100px;
      gap: 0.75rem;
    }
  }
  .tile {
    position: relative;
    aspect-ratio: 4 / 3;
    padding: 0;
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    background: #000;
    cursor: pointer;
  }
  .tile img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }
  .tile-offline {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--text-dim);
    font-size: 0.85rem;
  }
  .tile-label {
    position: absolute;
    left: 0.4rem;
    bottom: 0.4rem;
    padding: 0.1rem 0.4rem;
    border-radius: 4px;
    background: rgba(0, 0, 0, 0.6);
    color: #fff;
    font-size: 0.8rem;
    font-weight: 600;
  }
  .load-error {
    margin: 0 0.75rem 0.5rem;
    color: var(--err);
  }

  .live {
    position: fixed;
    inset: 0;
    z-index: 50;
    background: #000;
    display: flex;
    flex-direction: column;
  }
  .live video {
    flex: 1;
    width: 100%;
    min-height: 0;
    object-fit: contain;
    background: #000;
  }
  .live-bar {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.6rem 0.9rem;
    background: var(--surface);
    border-top: 1px solid var(--border);
  }
  .live-title {
    font-weight: 600;
    color: var(--text);
  }
  .live-note {
    color: var(--text-dim);
    font-size: 0.85rem;
  }
  .live-note.err {
    color: var(--err);
  }
  .live-close {
    margin-left: auto;
    padding: 0.4rem 0.9rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg);
    color: var(--text);
    cursor: pointer;
  }
</style>
