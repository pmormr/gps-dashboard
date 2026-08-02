<script lang="ts">
  import { onDestroy, onMount } from 'svelte'

  import type { Camera } from './api'
  import { alignTransform, CAM_RETRY_MS, type CamAlign, livePath } from './cameras'
  import { errMsg } from './errors'
  import { startWhep, whepEndpoint, type WhepSession } from './whep'

  // One live WHEP feed on the driving wall. Owns its own peer connection and a
  // bounded auto-reconnect: a driving safety view should heal a blip on its own
  // rather than stay dark. The parent remounts the tile to change resolution
  // (via {#key hd}), so `hd` is read once at connect — no reactive re-open here.
  interface Props {
    cam: Camera
    /** Use the 720p `-hd` feed; the parent decides (D1 default keeps a 3-up
     *  wall's simultaneous decode light). */
    hd: boolean
    /** Alignment window for the seamless 180° strip; when set, the tile drops its
     *  rounding/gap and applies the crop transform. Omit for a standalone tile. */
    align?: CamAlign
  }
  let { cam, hd, align }: Props = $props()

  let phase = $state<'connecting' | 'live' | 'error'>('connecting')
  let note = $state('')
  let videoEl = $state<HTMLVideoElement>()
  let session: WhepSession | null = null
  let retryTimer = 0
  let disposed = false

  async function connect(): Promise<void> {
    session?.close()
    session = null
    phase = 'connecting'
    note = ''
    try {
      const s = await startWhep(whepEndpoint(livePath(cam, hd)), {
        media: ['video'],
        onClosed: onDropped,
        unreachableMessage: 'Hub unreachable',
      })
      if (disposed) {
        s.close()
        return
      }
      session = s
      if (videoEl) {
        videoEl.srcObject = s.stream
        await videoEl.play().catch(() => {}) // autoplay+muted allows this; ignore edge rejections
      }
      phase = 'live'
    } catch (e) {
      note = errMsg(e)
      phase = 'error'
      scheduleRetry()
    }
  }

  function onDropped(): void {
    // The peer failed/disconnected on its own — reconnect after a beat.
    if (disposed || phase === 'connecting') return
    phase = 'error'
    note = 'Reconnecting…'
    scheduleRetry()
  }

  function scheduleRetry(): void {
    clearTimeout(retryTimer)
    retryTimer = window.setTimeout(() => {
      if (!disposed) void connect()
    }, CAM_RETRY_MS)
  }

  onMount(() => void connect())
  onDestroy(() => {
    disposed = true
    clearTimeout(retryTimer)
    session?.close()
    if (videoEl) videoEl.srcObject = null
  })
</script>

<div
  class="cell"
  class:offline={phase === 'error'}
  class:pano={!!align}
  style={align ? `flex-grow:${align.weight}` : undefined}
>
  <!-- svelte-ignore a11y_media_has_caption -->
  <video
    bind:this={videoEl}
    autoplay
    playsinline
    muted
    style={align ? `transform:${alignTransform(align)}` : undefined}
  ></video>
  <span class="label">{cam.label}</span>
  {#if phase !== 'live'}
    <div class="status">{phase === 'connecting' ? 'Connecting…' : note}</div>
  {/if}
</div>

<style>
  .cell {
    position: relative;
    min-height: 0;
    min-width: 0;
    background: #000;
    border-radius: 8px;
    overflow: hidden;
  }
  .cell.offline {
    outline: 2px solid var(--err);
    outline-offset: -2px;
  }
  /* Seamless strip member: no rounding, weight-proportioned width. */
  .cell.pano {
    border-radius: 0;
    flex-basis: 0;
  }
  video {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    background: #000;
    transform-origin: center;
  }
  .label {
    position: absolute;
    left: 0.4rem;
    top: 0.4rem;
    padding: 0.1rem 0.45rem;
    border-radius: 4px;
    background: rgba(0, 0, 0, 0.65);
    color: #fff;
    font-size: 0.85rem;
    font-weight: 700;
    letter-spacing: 0.02em;
  }
  .status {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text-dim);
    font-size: 0.9rem;
    background: var(--surface);
  }
</style>
