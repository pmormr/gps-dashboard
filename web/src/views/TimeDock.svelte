<script lang="ts">
  import { onMount } from 'svelte'

  import { annotations } from '../lib/stores/annotations.svelte'
  import { selection } from '../lib/stores/selection.svelte'
  import { track } from '../lib/stores/track.svelte'
  import { mountTimeStrip, type TimeStripHandle } from '../lib/timestrip'
  import './timeline.css'
  import TimePicker from './TimePicker.svelte'

  // The shared TimeDock (time-dock Phase 3): picker + nav cluster + TimeStrip +
  // status, rendered identically by Map (bottom overlay) and Trends (above the
  // chart) — one interaction grammar on one axis. It drives the shared track
  // store's window fetch and turns strip gestures into Selection-store commits;
  // map-specific rendering (trail, pins/range overlays, pendingPan) stays in
  // Map.svelte, reacting to the same stores. Annotation bands/ticks on the strip
  // are click-to-jump from either view.
  let { placement = 'up' }: { placement?: 'up' | 'down' } = $props()

  let canvas: HTMLCanvasElement
  let tooltip: HTMLElement
  let strip: TimeStripHandle | undefined
  let bookmarked = $state(false)

  function windowLabel(ms: number): string {
    return new Date(ms).toLocaleString([], {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const startLabel = $derived(windowLabel(selection.range.from.getTime()))
  const endLabel = $derived(windowLabel(selection.range.to.getTime()))

  // Name the current window — a range annotation is a saved window (time-dock plan).
  function saveWindow(): void {
    const r = selection.range
    annotations.openCreate(r.from.toISOString(), r.to.toISOString())
  }

  async function bookmarkHere(): Promise<void> {
    if (bookmarked) return
    try {
      await annotations.bookmarkCurrent()
      bookmarked = true
      setTimeout(() => (bookmarked = false), 1200)
    } catch (e) {
      alert(`Bookmark failed: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  onMount(() => {
    strip = mountTimeStrip(canvas, tooltip, {
      onZoom: (loMs, hiMs) => selection.zoomTo(new Date(loMs), new Date(hiMs)),
      onWiden: () => selection.widen(),
      onShift: (dir) => selection.shift(dir),
      onBack: () => selection.back(),
      onAnnotationClick: (a) =>
        annotations.jumpTo({ start_time: a.start_time, end_time: a.end_time ?? null }),
    })
    annotations.reload()
    return () => strip?.destroy()
  })

  // One fetch per window change, shared by every consumer (also fires on each
  // live tick — `range` recomputes and the key changes).
  $effect(() => {
    track.ensure(selection.range)
  })

  // Feed the strip the loaded window + its points and the annotation overlays.
  // The axis is the requested window, not the data extent (S1) — and it always
  // renders, even with zero points: an empty-density strip is still navigable
  // and its annotation bands stay clickable.
  $effect(() => {
    if (track.loadedToMs > track.loadedFromMs) {
      strip?.setData({
        startMs: track.loadedFromMs,
        endMs: track.loadedToMs,
        points: track.points,
        annotations: annotations.list,
      })
    }
  })
</script>

<div class="timeline">
  <div class="tl-time-row">
    <TimePicker {placement} />
    <div class="tl-nav">
      <button type="button" title="Back one window (←)" onclick={() => selection.shift(-1)}>◀</button>
      <button type="button" title="Widen ×2 (−)" onclick={() => selection.widen()}>⊖</button>
      <button
        type="button"
        title="Forward one window (→)"
        disabled={selection.live}
        onclick={() => selection.shift(1)}
      >▶</button>
      {#if selection.canGoBack}
        <button type="button" title="Back to the previous window (Backspace)" onclick={() => selection.back()}>↩</button>
      {/if}
      {#if selection.live}
        <span class="tl-live">LIVE</span>
      {:else}
        <button type="button" class="tl-golive" title="Go live — last 24h" onclick={() => selection.goLive()}>
          ⦿ LIVE
        </button>
      {/if}
    </div>
    <span class="tl-status">{track.status}</span>
  </div>

  <div class="tl-strip-wrap">
    <div class="tl-strip-canvas-wrap">
      <canvas class="tl-strip" bind:this={canvas}></canvas>
      <div class="tl-strip-tooltip hidden" bind:this={tooltip}></div>
    </div>
    <div class="tl-slider-labels">
      <span>{startLabel}</span>
      <span class="muted">{track.empty || 'drag to zoom · double-click to go back'}</span>
      <span>{endLabel}</span>
    </div>
  </div>

  <div class="tl-bottom-actions">
    {#if selection.live}
      <button class="btn-secondary" disabled={bookmarked} onclick={bookmarkHere}>
        {bookmarked ? '✓ Bookmarked' : '📍 Bookmark Here'}
      </button>
    {/if}
    <button class="btn-secondary" onclick={saveWindow}>💾 Save window</button>
  </div>
</div>
