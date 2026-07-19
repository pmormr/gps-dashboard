<script lang="ts">
  import type { Component } from 'svelte'
  import { errMsg } from '../lib/errors'
  import { onDestroy, onMount } from 'svelte'

  import {
    getFridgeHistory,
    getFridgeStatus,
    postFridge,
    type FridgeHistoryResponse,
    type FridgeStatus,
    type SensorSeriesResponse,
  } from '../lib/api'
  import { clampSetpoint, formatTemp, historyToSeries } from '../lib/fridge'
  import { ageSeconds, formatAge } from '../lib/sensors'
  import Toast from '../lib/Toast.svelte'
  import { useToast } from '../lib/useToast.svelte'

  // The chart stack (LayerCake + d3) is heavy; like Trends, load it only when
  // this view opens so the main bundle stays lean.
  type TrendProps = { resp: SensorSeriesResponse | null; height?: number }
  let TrendComp = $state<Component<TrendProps> | null>(null)

  const ZONES = [0, 1] as const
  const SPANS = [
    { v: 'hour', l: 'Hourly' },
    { v: 'day', l: 'Daily' },
    { v: 'week', l: 'Weekly' },
  ] as const
  const POWER_SOURCE: Record<number, string> = { 0: 'AC', 1: 'DC', 2: 'Solar' }
  const ALERTS: Array<{ col: string; label: string }> = [
    { col: 'temp_alert_cc', label: 'Temp alert' },
    { col: 'temp_alert_dcm', label: 'Temp alert (DCM)' },
    { col: 'door_alert', label: 'Door alert' },
    { col: 'voltage_alert', label: 'Voltage alert' },
  ]

  // A post-write read-back overlays the (≤60 s stale) DB snapshot until the
  // reader's next poll catches up or the overlay ages out.
  const OVERRIDE_TTL_MS = 120_000

  let s = $state<FridgeStatus | null>(null)
  let hist = $state<FridgeHistoryResponse | null>(null)
  let span = $state<'hour' | 'day' | 'week'>('hour')
  let drafts = $state<Record<number, number | null>>({ 0: null, 1: null })
  let overrides = $state<Record<string, { value: number; at: number }>>({})
  let pendingOff = $state<number | null>(null)
  let busy = $state(false)

  let pollTimer: number | undefined
  let histTimer: number | undefined

  const toaster = useToast(2600)

  /** A metric from the snapshot, preferring a fresh post-write read-back. */
  function val(col: string): number | null {
    const o = overrides[col]
    if (o && Date.now() - o.at < OVERRIDE_TTL_MS) return o.value
    const v = s?.reading?.[col]
    return typeof v === 'number' ? v : null
  }

  async function poll(): Promise<void> {
    try {
      const next = await getFridgeStatus()
      s = next
      // Drop overrides the reader has caught up with.
      for (const [col, o] of Object.entries(overrides)) {
        if (next.reading?.[col] === o.value) delete overrides[col]
      }
    } catch {
      s = null
    }
  }

  async function loadHistory(): Promise<void> {
    try {
      hist = await getFridgeHistory(span)
    } catch {
      hist = null
    }
  }

  function setSpan(next: 'hour' | 'day' | 'week'): void {
    span = next
    loadHistory()
  }

  function step(zone: 0 | 1, delta: number): void {
    const current = drafts[zone] ?? val(`comp${zone}_set_c`) ?? 0
    drafts[zone] = clampSetpoint(current + delta, s?.ranges?.[`comp${zone}`]?.allowed ?? null)
  }

  async function applySetpoint(zone: 0 | 1): Promise<void> {
    const draft = drafts[zone]
    if (draft == null || busy) return
    busy = true
    try {
      const back = await postFridge('/api/fridge/setpoint', { zone, temp_c: draft })
      if (typeof back.set_c === 'number') {
        overrides[`comp${zone}_set_c`] = { value: back.set_c, at: Date.now() }
      }
      drafts[zone] = null
      toaster.toast(`Zone ${zone + 1} set to ${formatTemp(draft, s?.temp_unit ?? null)}`)
    } catch (e) {
      toaster.toast(errMsg(e), true)
    } finally {
      busy = false
    }
  }

  async function applyPower(zone: 0 | 1, on: boolean): Promise<void> {
    if (busy) return
    busy = true
    try {
      const back = await postFridge('/api/fridge/power', { zone, on })
      if (typeof back.power === 'number') {
        overrides[`comp${zone}_power`] = { value: back.power, at: Date.now() }
      }
      toaster.toast(`Zone ${zone + 1} ${on ? 'on' : 'off'}`)
    } catch (e) {
      toaster.toast(errMsg(e), true)
    } finally {
      busy = false
      pendingOff = null
    }
  }

  function requestPowerOff(zone: 0 | 1): void {
    pendingOff = zone
  }

  onMount(() => {
    poll()
    loadHistory()
    import('../lib/charts/Trend.svelte').then((m) => (TrendComp = m.default))
    pollTimer = window.setInterval(poll, 15_000)
    histTimer = window.setInterval(loadHistory, 300_000)
  })
  onDestroy(() => {
    if (pollTimer) clearInterval(pollTimer)
    if (histTimer) clearInterval(histTimer)
  })

  const unit = $derived(s?.temp_unit ?? null)
  const dataAge = $derived(
    s?.reading ? formatAge(ageSeconds(s.reading.timestamp as string)) : null,
  )
  const series = $derived(hist && hist.points.length ? historyToSeries(hist) : null)

  const setDraftDisplay = (zone: 0 | 1): string =>
    formatTemp(drafts[zone] ?? val(`comp${zone}_set_c`), unit)
</script>

<header class="page-head">
  <h1>Fridge — Dometic CFX3 75DZ</h1>
  <p class="muted">DDMP control · zone setpoints, power, DC history</p>
</header>

{#if !s}
  <div class="status-banner err">Connecting…</div>
{:else if !s.online}
  <div class="status-banner err">
    Fridge offline — stream {s.status}{s.last_seen ? ` · last seen ${formatAge(ageSeconds(s.last_seen))} ago` : ''}.
    Controls need the fridge on the van WiFi.
  </div>
{:else}
  <div class="status-banner ok">● Online{dataAge ? ` · data ${dataAge} old` : ''}</div>
{/if}

{#each ZONES as zone (zone)}
  {@const measured = val(`comp${zone}_temp_c`)}
  {@const power = val(`comp${zone}_power`)}
  {@const door = val(`comp${zone}_door_open`)}
  <div class="card">
    <div class="zone-head">
      <div class="eyebrow">Zone {zone + 1}</div>
      <div class="badges">
        {#if door === 1}<span class="badge warn">door open</span>{/if}
        <span class="badge" class:ok={power === 1} class:dim={power === 0}>
          {power == null ? '—' : power === 1 ? 'on' : 'off'}
        </span>
      </div>
    </div>
    <div class="readout">
      <div class="temp">{formatTemp(measured, unit)}</div>
      <div class="target">
        set {formatTemp(val(`comp${zone}_set_c`), unit)}
      </div>
    </div>

    <div class="sub-label">Setpoint</div>
    <div class="row2">
      <button aria-label="Lower setpoint" onclick={() => step(zone, -1)}>−</button>
      <div class="draft" class:editing={drafts[zone] != null}>{setDraftDisplay(zone)}</div>
      <button aria-label="Raise setpoint" onclick={() => step(zone, 1)}>+</button>
      <button
        class="primary"
        disabled={drafts[zone] == null || busy}
        onclick={() => applySetpoint(zone)}
      >
        Set
      </button>
    </div>

    <div class="sub-label">Power</div>
    {#if pendingOff === zone}
      <div class="confirm">
        <span>Turn off Zone {zone + 1}? Contents will warm.</span>
        <div class="row2">
          <button onclick={() => (pendingOff = null)}>Cancel</button>
          <button class="danger" disabled={busy} onclick={() => applyPower(zone, false)}>
            Turn off
          </button>
        </div>
      </div>
    {:else if power === 0}
      <button class="primary" disabled={busy} onclick={() => applyPower(zone, true)}>
        Turn on
      </button>
    {:else}
      <button disabled={power == null || busy} onclick={() => requestPowerOff(zone)}>
        Turn off
      </button>
    {/if}
  </div>
{/each}

<div class="card">
  <div class="eyebrow">Power</div>
  <div class="grid3">
    <div class="cell">
      <div class="lbl">Source</div>
      <div class="valx">{POWER_SOURCE[val('power_source') ?? -1] ?? '—'}</div>
    </div>
    <div class="cell">
      <div class="lbl">Input</div>
      <div class="valx">{val('input_voltage_v')?.toFixed(1) ?? '—'}<span class="u">V</span></div>
    </div>
    <div class="cell">
      <div class="lbl">DC draw</div>
      <div class="valx">{val('dc_current_a')?.toFixed(1) ?? '—'}<span class="u">A</span></div>
    </div>
  </div>
  {#if ALERTS.some((a) => val(a.col) === 1)}
    <div class="alerts">
      {#each ALERTS as a (a.col)}
        {#if val(a.col) === 1}<span class="badge warn">{a.label}</span>{/if}
      {/each}
    </div>
  {/if}
</div>

<div class="card">
  <div class="eyebrow">DC power history</div>
  <div class="seg">
    {#each SPANS as sp (sp.v)}
      <button class:active={span === sp.v} onclick={() => setSpan(sp.v)}>{sp.l}</button>
    {/each}
  </div>
  {#if series && TrendComp}
    <div class="chart"><TrendComp resp={series} height={220} /></div>
  {:else}
    <p class="muted note">
      No stored history for this span yet — buckets accumulate as the reader polls.
    </p>
  {/if}
</div>

<Toast c={toaster} />

<style>
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 16px;
    padding: 14px;
  }
  .zone-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }
  .badges {
    display: flex;
    gap: 6px;
  }
  .badge {
    font-size: 11px;
    font-weight: 600;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 2px 8px;
    color: var(--text-dim);
  }
  .badge.ok {
    color: var(--ok);
    border-color: rgba(62, 207, 142, 0.4);
  }
  .badge.warn {
    color: var(--warn, #f59e0b);
    border-color: rgba(245, 158, 11, 0.4);
  }
  .badge.dim {
    opacity: 0.7;
  }

  .readout {
    display: flex;
    align-items: baseline;
    gap: 12px;
  }
  .temp {
    font-size: 40px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .target {
    font-size: 13px;
    color: var(--text-dim);
  }

  .sub-label {
    font-size: 12px;
    color: var(--text-dim);
    margin: 12px 0 6px;
  }
  .row2 {
    display: flex;
    gap: 8px;
  }
  .row2 > * {
    flex: 1;
  }
  .draft {
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 16px;
    font-variant-numeric: tabular-nums;
    min-width: 72px;
  }
  .draft.editing {
    border-color: var(--accent);
    color: var(--accent);
  }

  button {
    background: var(--surface-2);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 6px;
    padding: 9px 12px;
    font: inherit;
    font-size: 14px;
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  button.primary {
    background: var(--accent);
    border-color: var(--accent);
    color: #06121f;
    font-weight: 600;
  }
  button.danger {
    background: var(--err);
    border-color: var(--err);
    color: #fff;
    font-weight: 600;
  }
  .confirm {
    background: rgba(239, 83, 80, 0.08);
    border: 1px solid rgba(239, 83, 80, 0.35);
    border-radius: 8px;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    font-size: 13px;
  }

  .seg {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-top: 10px;
  }
  .seg button {
    flex: 1;
    min-width: 60px;
  }
  .seg button.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #06121f;
    font-weight: 600;
  }
  .chart {
    margin-top: 12px;
  }

  .grid3 {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    margin-top: 10px;
  }
  .cell .lbl {
    font-size: 11px;
    color: var(--text-dim);
  }
  .cell .valx {
    font-size: 20px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .cell .u {
    font-size: 12px;
    color: var(--text-dim);
    margin-left: 3px;
  }
  .alerts {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-top: 12px;
  }

  .note {
    margin-top: 12px;
    font-size: 13px;
  }

</style>
