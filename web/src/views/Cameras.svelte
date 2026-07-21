<script lang="ts">
  import { onDestroy, onMount } from 'svelte'

  import { getCameras, type Camera } from '../lib/api'
  import { SNAPSHOT_REFRESH_MS, livePath, snapshotUrl } from '../lib/cameras'
  import { errMsg } from '../lib/errors'
  import { acquireWakeLock, releaseWakeLock } from '../lib/wakelock'
  import { startWhep, whepEndpoint, type WhepSession } from '../lib/whep'

  // Glance-first grid: each tile is a downscaled JPEG still, refreshed per-tile.
  // Refresh is self-scheduled (next fetch fires only after the current one loads,
  // never mid-flight) so a slow tile just refreshes less often instead of being
  // cancelled every cycle — and the grid self-throttles to available bandwidth.
  // Tapping a tile opens the live 720p WHEP feed.
  let cameras = $state<Camera[]>([])
  let loadError = $state('')
  let bust = $state<Record<string, number>>({}) // node → cache-bust stamp
  let offline = $state<Record<string, boolean>>({}) // node → last snapshot failed
  const timers: Record<string, number> = {} // node → pending refresh timer

  // Live overlay (tap a tile → 720p WHEP video over the Phase 2 client).
  let liveCam = $state<Camera | null>(null)
  let liveState = $state<'connecting' | 'live' | 'error'>('connecting')
  let liveErr = $state('')
  let videoEl = $state<HTMLVideoElement>()
  let session: WhepSession | null = null

  function kick(node: string): void {
    bust = { ...bust, [node]: (bust[node] ?? 0) + 1 }
  }
  function kickAll(): void {
    for (const c of cameras) kick(c.node)
  }
  function scheduleRefresh(node: string): void {
    clearTimeout(timers[node])
    // Pause while a feed is up (grid hidden) or the tab is backgrounded; the
    // close / visibility handlers re-kick to resume.
    timers[node] = window.setTimeout(() => {
      if (!liveCam && !document.hidden) kick(node)
    }, SNAPSHOT_REFRESH_MS)
  }

  function onImgLoad(node: string): void {
    if (offline[node]) offline = { ...offline, [node]: false }
    scheduleRefresh(node)
  }
  function onImgError(node: string): void {
    if (!offline[node]) offline = { ...offline, [node]: true }
    scheduleRefresh(node) // keep retrying on the same cadence
  }
  function onVisible(): void {
    if (document.visibilityState === 'visible' && !liveCam) kickAll()
  }

  onMount(() => {
    getCameras()
      .then((r) => {
        cameras = r.cameras
        bust = Object.fromEntries(r.cameras.map((c) => [c.node, 1])) // first load per tile
      })
      .catch((e) => (loadError = errMsg(e)))
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      document.removeEventListener('visibilitychange', onVisible)
      for (const id of Object.values(timers)) clearTimeout(id)
    }
  })
  onDestroy(() => stopLive())

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

  function stopLive(): void {
    session?.close()
    session = null
    if (videoEl) videoEl.srcObject = null
    liveCam = null
    releaseWakeLock()
  }
  function closeLive(): void {
    stopLive()
    if (document.visibilityState === 'visible') kickAll() // resume the grid
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
      <img src={snapshotUrl(cam.node, bust[cam.node] ?? 0)} alt={cam.label} onload={() => onImgLoad(cam.node)} onerror={() => onImgError(cam.node)} />
      {#if offline[cam.node]}<div class="tile-offline">No image</div>{/if}
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
  /* Opaque overlay covers the broken-image glyph while a tile is offline; the
     img keeps loading underneath, so a recovered camera clears it on next load. */
  .tile-offline {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--surface);
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
