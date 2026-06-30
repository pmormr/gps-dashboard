<script lang="ts">
  import { PRESETS, selection, type Mode } from '../lib/stores/selection.svelte'

  // Graylog-style window picker (trigger + popover), ported from timepicker.js. It
  // edits a local draft and commits to the global Selection store on Apply; presets
  // commit immediately. Self-contained: reads `selection.label` for the trigger and
  // writes via `selection.setPicker`.

  // `placement` picks the desktop popover direction: 'up' for a bottom-anchored
  // trigger (the Map timeline), 'down' for a top-anchored one (Trends). Mobile is a
  // bottom sheet regardless.
  let { placement = 'up' }: { placement?: 'up' | 'down' } = $props()

  let open = $state(false)
  let mode = $state<Mode>('last')
  let anchorStr = $state('')
  let windowNum = $state(24)
  let windowUnit = $state(3600000) // ms per unit
  let live = $state(true)
  let fromStr = $state('')
  let toStr = $state('')

  const UNITS = [
    { label: 'minutes', ms: 60000 },
    { label: 'hours', ms: 3600000 },
    { label: 'days', ms: 86400000 },
    { label: 'weeks', ms: 604800000 },
  ]
  const MODES: { id: Mode; label: string }[] = [
    { id: 'last', label: 'Last' },
    { id: 'around', label: 'Around' },
    { id: 'range', label: 'From → To' },
  ]

  /** <input type="datetime-local"> wants local-tz YYYY-MM-DDTHH:MM (not UTC). */
  function dtLocal(d: Date): string {
    const pad = (n: number): string => String(n).padStart(2, '0')
    return (
      `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
      `T${pad(d.getHours())}:${pad(d.getMinutes())}`
    )
  }

  /** ms window → [count, unit-ms], picking the coarsest exact unit. */
  function splitWindow(ms: number): [number, number] {
    if (ms % 604800000 === 0) return [ms / 604800000, 604800000]
    if (ms % 86400000 === 0) return [ms / 86400000, 86400000]
    if (ms % 3600000 === 0) return [ms / 3600000, 3600000]
    return [Math.max(1, Math.round(ms / 60000)), 60000]
  }

  function openPop(): void {
    mode = selection.mode
    const a = selection.anchor ?? new Date()
    anchorStr = dtLocal(a)
    const [num, unit] = splitWindow(selection.windowMs)
    windowNum = num
    windowUnit = unit
    live = selection.live
    fromStr = dtLocal(selection.from ?? new Date(Date.now() - selection.windowMs))
    toStr = dtLocal(selection.to ?? new Date())
    open = true
  }

  function toggle(): void {
    if (open) open = false
    else openPop()
  }

  function selectMode(m: Mode): void {
    mode = m
    if (m === 'range') {
      if (!fromStr) fromStr = dtLocal(new Date(Date.now() - windowNum * windowUnit))
      if (!toStr) toStr = dtLocal(new Date())
    }
    if (m !== 'last') live = false
  }

  function apply(): void {
    const windowMs = (windowNum > 0 ? windowNum : 1) * windowUnit
    if (mode === 'range') {
      const from = fromStr ? new Date(fromStr) : null
      const to = toStr ? new Date(toStr) : null
      if (from && to && from >= to) {
        alert('"From" must be before "To".')
        return
      }
      selection.setPicker({ mode, from, to, live: false })
    } else {
      selection.setPicker({ mode, anchor: new Date(anchorStr), windowMs, live })
    }
    open = false
  }

  function applyPreset(ms: number): void {
    selection.setPicker({ mode: 'last', windowMs: ms, live: true, anchor: null })
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
      <div class="timepicker-modes">
        {#each MODES as m (m.id)}
          <button type="button" class:active={mode === m.id} onclick={() => selectMode(m.id)}>
            {m.label}
          </button>
        {/each}
      </div>

      <div class="timepicker-presets">
        {#each PRESETS as p (p.ms)}
          <button type="button" onclick={() => applyPreset(p.ms)}>{p.label}</button>
        {/each}
      </div>

      {#if mode !== 'range'}
        <div class="timepicker-fields">
          <label>
            <span>Anchor</span>
            <div class="timepicker-anchor-row">
              <input type="datetime-local" bind:value={anchorStr} />
              <button type="button" class="btn-secondary" onclick={() => (anchorStr = dtLocal(new Date()))}>
                Now
              </button>
            </div>
          </label>
          <label>
            <span>Window</span>
            <div class="timepicker-window-row">
              <input type="number" min="1" step="1" bind:value={windowNum} />
              <select bind:value={windowUnit}>
                {#each UNITS as u (u.ms)}
                  <option value={u.ms}>{u.label}</option>
                {/each}
              </select>
            </div>
          </label>
          <label class="timepicker-live-row" class:disabled={mode !== 'last'}>
            <input type="checkbox" bind:checked={live} disabled={mode !== 'last'} />
            <span>Live — anchor follows now, auto-refresh</span>
          </label>
        </div>
      {:else}
        <div class="timepicker-fields">
          <label><span>From</span><input type="datetime-local" bind:value={fromStr} /></label>
          <label><span>To</span><input type="datetime-local" bind:value={toStr} /></label>
        </div>
      {/if}

      <div class="timepicker-actions">
        <button type="button" class="btn-secondary" onclick={() => (open = false)}>Cancel</button>
        <button type="button" class="btn-primary" onclick={apply}>Apply</button>
      </div>
    </div>
  {/if}
</div>
