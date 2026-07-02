<script lang="ts">
  import type { Annotation } from '../lib/api'
  import { annotations } from '../lib/stores/annotations.svelte'
  import { PRESETS, selection } from '../lib/stores/selection.svelte'

  // Compact window picker (trigger + popover) for the Selection axis. No mode
  // tabs, no Around, no Last-window form — presets and
  // the Live toggle commit immediately; the From→To pair is the one staged edit
  // (two fields need an explicit Jump). Everything finer-grained is direct
  // manipulation on the strip. Saved windows (annotations) list here too — the
  // axis-level jump, reachable from any view that renders the dock.

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
    annotations.reload()
    open = true
  }

  /** Short "when" line for a saved-window row. */
  function annWhen(a: Annotation): string {
    const opts: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric' }
    const start = new Date(a.start_time).toLocaleDateString([], opts)
    if (!a.end_time) return start
    const end = new Date(a.end_time).toLocaleDateString([], opts)
    return start === end ? start : `${start} → ${end}`
  }

  function jumpAnn(a: Annotation): void {
    annotations.jumpTo(a)
    open = false
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

      {#if annotations.list.length}
        <div class="timepicker-saved">
          <div class="timepicker-saved-head">Saved windows</div>
          <div class="timepicker-saved-list">
            {#each annotations.list as a (a.id)}
              <button type="button" onclick={() => jumpAnn(a)}>
                <span class="tp-ann-name">{a.end_time ? '▭' : '📍'} {a.name}</span>
                <span class="tp-ann-when">{annWhen(a)}</span>
              </button>
            {/each}
          </div>
        </div>
      {/if}

      <div class="timepicker-actions">
        <button type="button" class="btn-secondary" onclick={() => (open = false)}>Close</button>
        <button type="button" class="btn-primary" onclick={jump}>Jump</button>
      </div>
    </div>
  {/if}
</div>
