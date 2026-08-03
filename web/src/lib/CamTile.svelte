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
    /** Align mode: enable direct-manipulation gestures (drag = pan, wheel/pinch =
     *  zoom) that mutate `align` in place. */
    editing?: boolean
    /** Highlight this tile as the one the toolbar's width/flip controls act on. */
    selected?: boolean
    /** Grabbed in edit mode — the parent marks this tile selected. */
    onGrab?: () => void
    /** A gesture changed `align` — the parent persists. */
    onChange?: () => void
  }
  let { cam, hd, align, editing = false, selected = false, onGrab, onChange }: Props = $props()

  // Direct-manipulation crop (Align mode). Pointer events unify mouse/touch/pen:
  // one pointer drags (pan), two pinch (zoom). Pan is clamped to the zoom's
  // overscan so it can only reposition what's actually cropped — at 1× (full
  // frame) there's nothing to pan, which is correct.
  let cellEl = $state<HTMLDivElement>()
  const pointers = new Map<number, { x: number; y: number }>()
  let pinchDist = 0
  let pinchScale = 1

  const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v))

  function clampPan(): void {
    if (!align) return
    const lim = Math.max(0, (align.scale - 1) / 2)
    align.panX = clamp(align.panX, -lim, lim)
    align.panY = clamp(align.panY, -lim, lim)
  }

  function onPointerDown(e: PointerEvent): void {
    if (!editing || !align) return
    onGrab?.()
    cellEl?.setPointerCapture(e.pointerId)
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()]
      pinchDist = Math.hypot(a.x - b.x, a.y - b.y)
      pinchScale = align.scale
    }
  }

  function onPointerMove(e: PointerEvent): void {
    if (!editing || !align || !pointers.has(e.pointerId)) return
    const prev = pointers.get(e.pointerId)!
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
    const rect = cellEl?.getBoundingClientRect()
    if (!rect) return
    if (pointers.size >= 2) {
      const [a, b] = [...pointers.values()]
      const dist = Math.hypot(a.x - b.x, a.y - b.y)
      if (pinchDist > 0) align.scale = clamp(pinchScale * (dist / pinchDist), 1, 3)
    } else {
      align.panX += (e.clientX - prev.x) / rect.width
      align.panY += (e.clientY - prev.y) / rect.height
    }
    clampPan()
    onChange?.()
  }

  function onPointerUp(e: PointerEvent): void {
    pointers.delete(e.pointerId)
    if (pointers.size < 2) pinchDist = 0
  }

  function onWheel(e: WheelEvent): void {
    if (!editing || !align) return
    e.preventDefault() // zoom the tile, don't scroll the page
    align.scale = clamp(align.scale - e.deltaY * 0.002, 1, 3)
    clampPan()
    onChange?.()
  }

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
  bind:this={cellEl}
  class="cell"
  class:offline={phase === 'error'}
  class:pano={!!align}
  class:editing
  class:selected={editing && selected}
  style={align ? `flex-grow:${align.weight}` : undefined}
  role="group"
  aria-label={cam.label}
  onpointerdown={onPointerDown}
  onpointermove={onPointerMove}
  onpointerup={onPointerUp}
  onpointercancel={onPointerUp}
  onwheel={onWheel}
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
  /* Align mode: draggable, and don't let touch drags scroll/zoom the page. */
  .cell.editing {
    cursor: grab;
    touch-action: none;
  }
  .cell.editing:active {
    cursor: grabbing;
  }
  .cell.selected {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
    z-index: 1;
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
