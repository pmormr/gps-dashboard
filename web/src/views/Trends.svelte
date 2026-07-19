<script lang="ts">
  // Trends — the configurable graph explorer.
  // A registry-driven metric picker over any sensor channel, overlaid on one
  // bucketed/aligned chart, scoped by the global Selection time axis (so picking a
  // trip's window — e.g. via the map/annotations — re-scopes these charts too).
  import { onMount, type Component } from 'svelte'
  import { errMsg } from '../lib/errors'

  import { getSensors, getSensorSeries } from '../lib/api'
  import type { SensorSeriesResponse, SensorsResponse } from '../lib/api'
  import { selection } from '../lib/stores/selection.svelte'
  import { DOMAIN_LABELS, metricKeysFor, metricMeta, orderedSensors } from '../lib/sensors'
  import TimeDock from './TimeDock.svelte'

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
    showBand?: boolean
    hidden?: Set<string>
    height?: number
    onzoom?: (fromMs: number, toMs: number) => void
    onresetzoom?: () => void
  }
  let TrendComp = $state<Component<TrendProps> | null>(null)

  let reg = $state<SensorsResponse | null>(null)
  let resp = $state<SensorSeriesResponse | null>(null)
  let selected = $state<string[]>([])
  let hidden = $state(new Set<string>())
  let smoothWindow = $state(1)
  let showBand = $state(false)
  let error = $state<string | null>(null)

  // ── Drag-to-zoom: each region select narrows the global window (the Map follows
  // the same axis); the store's zoom history lets us step back out. ──
  function zoomIn(fromMs: number, toMs: number): void {
    selection.zoomTo(new Date(fromMs), new Date(toMs))
  }

  /** Double-click on the chart — pop straight back to the pre-zoom window. */
  function resetZoom(): void {
    selection.resetZoom()
  }

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

  // ── Saved presets (localStorage): a named metric set + its smoothing/band ──
  interface Preset {
    name: string
    metrics: string[]
    smoothWindow: number
    showBand: boolean
  }
  const PRESETS_KEY = 'trends.presets'

  function loadPresets(): Preset[] {
    try {
      const raw = localStorage.getItem(PRESETS_KEY)
      const data = raw ? JSON.parse(raw) : []
      return Array.isArray(data) ? data : []
    } catch {
      return []
    }
  }

  let presets = $state<Preset[]>(loadPresets())

  function persistPresets(): void {
    try {
      localStorage.setItem(PRESETS_KEY, JSON.stringify(presets))
    } catch {
      // Private mode / quota — presets just don't survive the session.
    }
  }

  function savePreset(): void {
    const name = prompt('Preset name?')?.trim()
    if (!name || selected.length === 0) return
    presets = [...presets.filter((p) => p.name !== name), { name, metrics: [...selected], smoothWindow, showBand }]
    persistPresets()
  }

  function applyPreset(p: Preset): void {
    selected = [...p.metrics]
    smoothWindow = p.smoothWindow
    showBand = p.showBand
    hidden = new Set()
  }

  function deletePreset(name: string): void {
    presets = presets.filter((p) => p.name !== name)
    persistPresets()
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
      error = errMsg(e)
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
        if (!cancelled) error = errMsg(e)
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
    {#if error}<span class="err-text">{error}</span>{:else}Graph any sensor channel over time · drag across the chart to zoom in{/if}
  </p>
</header>

<!-- The shared TimeDock: same axis + interaction grammar as the Map's bottom bar;
     its density lane shows GPS activity ("when was I driving") with no map on
     screen. -->
<section class="dock-panel">
  <TimeDock placement="down" />
</section>

<div class="bar">
  <label class="smooth">
    Smoothing
    <select bind:value={smoothWindow}>
      {#each SMOOTHING as s (s.window)}
        <option value={s.window}>{s.label}</option>
      {/each}
    </select>
  </label>
  <label class="toggle">
    <input type="checkbox" bind:checked={showBand} />
    Min/max band
  </label>
</div>

<div class="presets">
  {#each presets as p (p.name)}
    <span class="preset">
      <button class="preset-load" onclick={() => applyPreset(p)}>{p.name}</button>
      <button class="preset-del" onclick={() => deletePreset(p.name)} aria-label="Delete {p.name}">×</button>
    </span>
  {/each}
  <button class="preset-save" onclick={savePreset} disabled={selected.length === 0}>+ Save preset</button>
</div>

<section class="panel chart-panel">
  {#if TrendComp}
    <TrendComp {resp} {smoothWindow} {showBand} {hidden} height={320} onzoom={zoomIn} onresetzoom={resetZoom} />
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
    <div class="grp eyebrow">Channels</div>
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
  .toggle {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--text-dim);
    cursor: pointer;
  }
  .dock-panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 10px 12px;
    margin-bottom: 12px;
  }

  .presets {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-bottom: 14px;
  }
  .preset {
    display: inline-flex;
    align-items: center;
    border: 1px solid var(--border);
    border-radius: 999px;
    overflow: hidden;
  }
  .preset-load {
    padding: 5px 6px 5px 12px;
    background: var(--surface);
    color: var(--text);
    border: none;
    font: inherit;
    font-size: 12px;
    cursor: pointer;
  }
  .preset-load:hover {
    background: var(--border);
  }
  .preset-del {
    padding: 5px 9px 5px 4px;
    background: var(--surface);
    color: var(--text-dim);
    border: none;
    font: inherit;
    font-size: 14px;
    line-height: 1;
    cursor: pointer;
  }
  .preset-del:hover {
    color: var(--err);
  }
  .preset-save {
    padding: 5px 12px;
    background: none;
    color: var(--accent);
    border: 1px dashed var(--border);
    border-radius: 999px;
    font: inherit;
    font-size: 12px;
    cursor: pointer;
  }
  .preset-save:disabled {
    opacity: 0.45;
    cursor: default;
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
