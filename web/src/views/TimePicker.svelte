<script lang="ts">
  import { PRESETS, selection } from '../lib/stores/selection.svelte'

  // Compact window picker (trigger + popover) for the Selection axis. No mode
  // tabs, no Around, no Last-window form (time-dock plan Phase 1) — presets and
  // the Live toggle commit immediately; the From→To pair is the one staged edit
  // (two fields need an explicit Jump). Everything finer-grained is direct
  // manipulation on the strip.

  // `placement` picks the desktop popover direction: 'up' for a bottom-anchored
  // trigger (the Map timeline), 'down' for a top-anchored one (Trends). Mobile is a
  // bottom sheet regardless.
  let { placement = 'up' }: { placement?: 'up' | 'down' } = $props()

  let open = $state(false)
  let fromStr = $state('')
  let toStr = $state('')

  /** <input type="datetime-local"> wants local-tz YYYY-MM-DDTHH:MM (not UTC). */
  function dtLocal(d: Date): string {
    const pad = (n: number): string => String(n).padStart(2, '0')
    return (
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
      `T${pad(d.getHours())}:${pad(d.getMinutes())}`
    )
  }

  function openPop(): void {
    const r = selection.range
    fromStr = dtLocal(r.from)
    toStr = dtLocal(r.to)
    open = true
  }

  function toggle(): void {
    if (open) open = false
    else openPop()
  }

  /** Preset chip — a live trailing window of the given width. Immediate. */
  function applyPreset(ms: number): void {
    selection.setPicker({ mode: 'last', windowMs: ms, live: true, anchor: null })
    open = false
  }

  /** Live on = resume following now at the current window width; off = freeze here. */
  function toggleLive(): void {
    if (selection.live) {
      selection.setPicker({ mode: 'last', anchor: new Date(), live: false })
    } else {
      selection.setPicker({
        mode: 'last',
        windowMs: selection.range.windowMs,
        anchor: null,
        live: true,
      })
    }
  }

  /** Absolute jump to the staged From→To pair. */
  function jump(): void {
    const from = fromStr ? new Date(fromStr) : null
    const to = toStr ? new Date(toStr) : null
    if (!from || !to || from >= to) {
      alert('"From" must be before "To".')
      return
    }
    selection.setRange(from, to)
    open = false
  }
</script>

<div class="tp">
  <button class="timepicker-trigger" type="button" onclick={toggle}>
    <span class="timepicker-trigger-icon">⏱</span>
    <span class="timepicker-trigger-label">{selection.label}</span>
    <span class="timepicker-trigger-caret">▾</span>
  </button>

  {#if open}
    <!-- Backdrop: closes on outside click (and is the dim layer on mobile). -->
    <div
      class="timepicker-backdrop"
      role="presentation"
      onclick={() => (open = false)}
    ></div>
    <div class="timepicker-card" class:down={placement === 'down'}>
      <div class="timepicker-presets">
        {#each PRESETS as p (p.ms)}
          <button type="button" onclick={() => applyPreset(p.ms)}>{p.label}</button>
        {/each}
      </div>

      <label class="timepicker-live-row">
        <input type="checkbox" checked={selection.live} onchange={toggleLive} />
        <span>Live — window follows now, auto-refresh</span>
      </label>

      <div class="timepicker-fields">
        <label><span>From</span><input type="datetime-local" bind:value={fromStr} /></label>
        <label><span>To</span><input type="datetime-local" bind:value={toStr} /></label>
      </div>

      <div class="timepicker-actions">
        <button type="button" class="btn-secondary" onclick={() => (open = false)}>Close</button>
        <button type="button" class="btn-primary" onclick={jump}>Jump</button>
      </div>
    </div>
  {/if}
</div>
