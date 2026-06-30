<script lang="ts">
  // Trends — the configurable graph explorer (plans/sensor-graphing-plan.md).
  // A registry-driven metric picker over any sensor channel, overlaid on one
  // bucketed/aligned chart, scoped by the global Selection time axis (so picking a
  // trip's window — e.g. via the map/annotations — re-scopes these charts too).
  import { onMount, type Component } from 'svelte'

  import { getSensors, getSensorSeries } from '../lib/api'
  import type { SensorSeriesResponse, SensorsResponse } from '../lib/api'
  import { selection } from '../lib/stores/selection.svelte'
  import { DOMAIN_LABELS, metricKeysFor, metricMeta, orderedSensors } from '../lib/sensors'
  import TimePicker from './TimePicker.svelte'

  // Smoothing presets — a moving-average window in buckets (Off = no smoothing).
  const SMOOTHING = [
    { label: 'Off', window: 1 },
    { label: 'Light', window: 3 },
    { label: 'Medium', window: 7 },
    { label: 'Strong', window: 15 },
  ]

  // The chart (LayerCake + d3) is dynamic-imported so it forms its own chunk,
  // loaded only when Trends opens — the main bundle stays lean (frontend.md ethos).
  type TrendProps = {
    resp: SensorSeriesResponse | null
    smoothWindow?: number
    hidden?: Set<string>
    height?: number
  }
  let TrendComp = $state<Component<TrendProps> | null>(null)

  let reg = $state<SensorsResponse | null>(null)
  let resp = $state<SensorSeriesResponse | null>(null)
  let selected = $state<string[]>([])
  let hidden = $state(new Set<string>())
  let smoothWindow = $state(1)
  let error = $state<string | null>(null)

  /** Chartable channels for a sensor as `[address, label, color]`, picker order. */
  function channelsFor(r: SensorsResponse, sensorId: number): [string, string, string][] {
    const sensor = r.sensors.find((s) => s.id === sensorId)
    if (!sensor) return []
    return metricKeysFor(r, sensor)
      .filter((k) => metricMeta(r.meta, k).chart)
      .map((k) => {
        const m = metricMeta(r.meta, k)
        return [`${sensorId}.${k}`, m.label, m.color] as [string, string, string]
      })
  }

  function toggle(addr: string): void {
    selected = selected.includes(addr) ? selected.filter((a) => a !== addr) : [...selected, addr]
  }

  function toggleHidden(metric: string): void {
    const next = new Set(hidden)
    if (next.has(metric)) next.delete(metric)
    else next.add(metric)
    hidden = next
  }

  onMount(async () => {
    import('../lib/charts/Trend.svelte').then((m) => (TrendComp = m.default))
    try {
      const r = await getSensors()
      reg = r
      // Default to house battery voltage if present, else the first chartable channel.
      const vic = r.sensors.find((s) => s.type === 'victron')
      const first = orderedSensors(r.sensors)[0]
      const fallback = first ? channelsFor(r, first.id)[0]?.[0] : undefined
      const def = vic ? `${vic.id}.battery_voltage` : fallback
      if (def) selected = [def]
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  })

  // Refetch whenever the metric set or the global window changes (live tick included).
  $effect(() => {
    const range = selection.range
    const metrics = selected
    if (metrics.length === 0) {
      resp = { start: '', end: '', bucket_ms: 0, x: [], series: [] }
      return
    }
    const from = range.from.toISOString()
    const to = range.to.toISOString()
    let cancelled = false
    getSensorSeries(metrics, from, to, 800)
      .then((d) => {
        if (!cancelled) {
          resp = d
          error = null
        }
      })
      .catch((e) => {
        if (!cancelled) error = e instanceof Error ? e.message : String(e)
      })
    return () => {
      cancelled = true
    }
  })

  const sensors = $derived(reg ? orderedSensors(reg.sensors) : [])
</script>

<header class="page-head">
  <h1>Trends</h1>
  <p class="muted">
    {#if error}<span class="err-text">{error}</span>{:else}Graph any sensor channel over time{/if}
  </p>
</header>

<div class="bar">
  <TimePicker placement="down" />
  <label class="smooth">
    Smoothing
    <select bind:value={smoothWindow}>
      {#each SMOOTHING as s (s.window)}
        <option value={s.window}>{s.label}</option>
      {/each}
    </select>
  </label>
</div>

<section class="panel chart-panel">
  {#if TrendComp}
    <TrendComp {resp} {smoothWindow} {hidden} height={320} />
  {:else}
    <div class="chart-loading">Loading chart…</div>
  {/if}
  {#if resp && resp.series.length}
    <div class="legend">
      {#each resp.series as s (s.metric)}
        <button
          class="chip"
          class:off={hidden.has(s.metric)}
          onclick={() => toggleHidden(s.metric)}
        >
          <span class="sw" style:background={s.color}></span>
          {s.label}
        </button>
      {/each}
    </div>
  {/if}
</section>

{#if reg}
  <section class="panel">
    <div class="grp">Channels</div>
    {#each sensors as sensor (sensor.id)}
      {@const channels = channelsFor(reg, sensor.id)}
      {#if channels.length}
        <div class="src">
          <div class="src-name">{DOMAIN_LABELS[sensor.type] ?? sensor.node}</div>
          <div class="chips">
            {#each channels as [addr, label, color] (addr)}
              <button
                class="chip"
                class:active={selected.includes(addr)}
                onclick={() => toggle(addr)}
              >
                <span class="sw" style:background={color}></span>{label}
              </button>
            {/each}
          </div>
        </div>
      {/if}
    {/each}
  </section>
{/if}

<style>
  .err-text {
    color: var(--err);
  }
  .bar {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 12px;
  }
  .smooth {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--text-dim);
  }
  .smooth select {
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 5px 8px;
    font: inherit;
  }

  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 16px;
    padding: 14px;
  }
  .chart-panel {
    padding: 10px 12px 12px;
  }
  .chart-loading {
    display: grid;
    place-items: center;
    height: 320px;
    color: var(--text-dim);
    font-size: 13px;
  }

  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 8px;
  }

  .grp {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-dim);
    margin-bottom: 10px;
  }
  .src {
    margin-bottom: 12px;
  }
  .src-name {
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 6px;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 10px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--bg);
    color: var(--text-dim);
    font: inherit;
    font-size: 12px;
    cursor: pointer;
  }
  .chip.active {
    color: var(--text);
    border-color: var(--accent);
    background: color-mix(in srgb, var(--accent) 12%, var(--bg));
  }
  .chip.off {
    opacity: 0.45;
  }
  .sw {
    width: 9px;
    height: 9px;
    border-radius: 2px;
    flex-shrink: 0;
  }
</style>
