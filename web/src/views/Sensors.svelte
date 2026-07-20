<script lang="ts">
  // Sensors — the per-node telemetry detail, split out of the Systems hub. Each
  // chartable metric carries an inline sparkline and taps through to full Trends
  // (the glance↔history bridge: the harder channels only read against time).
  import { getSensors, getSensorSeries } from '../lib/api'
  import type { SensorsResponse, TrendSeries } from '../lib/api'
  import Sparkline from '../lib/charts/Sparkline.svelte'
  import { poll } from '../lib/poll.svelte'
  import { router } from '../lib/router.svelte'
  import { trendsHandoff } from '../lib/stores/trends.svelte'
  import {
    DOMAIN_LABELS,
    ageSeconds,
    dotClass,
    formatAge,
    formatValue,
    groupLabel,
    groupedKeys,
    metricKeysFor,
    metricMeta,
    orderedSensors,
  } from '../lib/sensors'

  const feed = poll<SensorsResponse>(getSensors, 30000)
  let data = $derived(feed.data)
  let error = $derived(feed.error)

  const sensors = $derived(data ? orderedSensors(data.sensors) : [])

  // Sparkline data: ONE batched series call for every chartable channel over a
  // fixed recent window (the server fans out one query per sensor internally) —
  // never one fetch per sparkline. Fixed 12 h so the page is self-contained,
  // independent of wherever the Trends global axis was last zoomed.
  const SPARK_HOURS = 12
  const SPARK_BUCKETS = 60
  let seriesByAddr = $state(new Map<string, TrendSeries>())

  async function loadSeries(d: SensorsResponse): Promise<void> {
    const addrs: string[] = []
    for (const s of orderedSensors(d.sensors)) {
      for (const key of metricKeysFor(d, s)) {
        if (metricMeta(d.meta, key).chart) addrs.push(`${s.id}.${key}`)
      }
    }
    if (!addrs.length) return
    const to = new Date()
    const from = new Date(to.getTime() - SPARK_HOURS * 3600 * 1000)
    try {
      const resp = await getSensorSeries(addrs, from.toISOString(), to.toISOString(), SPARK_BUCKETS)
      const m = new Map<string, TrendSeries>()
      for (const s of resp.series) m.set(s.metric, s)
      seriesByAddr = m
    } catch {
      // Sparklines are context, not load-bearing — a failed fetch just leaves them blank.
    }
  }

  // Load once when the registry first arrives, then refresh on a slow timer (the
  // 30 s registry poll keeps the live values fresh; sparklines can lag).
  let seriesLoaded = false
  $effect(() => {
    const d = data
    if (d && !seriesLoaded) {
      seriesLoaded = true
      loadSeries(d)
    }
  })
  $effect(() => {
    const timer = window.setInterval(() => {
      if (data) loadSeries(data)
    }, 120000)
    return () => clearInterval(timer)
  })

  function openTrend(addr: string): void {
    trendsHandoff.open([addr])
    router.navigate('/trends')
  }
</script>

<header class="page-head">
  <h1>Sensors</h1>
  <p class="muted">
    {#if error}<span class="err-text">{error}</span>{:else}Live telemetry · tap a channel to graph it{/if}
  </p>
</header>

{#if data}
  {#each sensors as s (s.id)}
    {@const groups = groupedKeys(data.meta, metricKeysFor(data, s))}
    {@const latest = s.latest}
    <section class="panel">
      <div class="sec-head">
        <span class="dot {dotClass(s)}"></span>
        <span class="sec-title">{DOMAIN_LABELS[s.type] ?? s.node}</span>
        <span class="sec-age muted">{s.status} · {formatAge(ageSeconds(s.last_seen))}</span>
      </div>
      {#each groups as [group, keys] (group)}
        {#if groups.length > 1 && group}
          <div class="grp eyebrow">{groupLabel(group)}</div>
        {/if}
        <div class="grid">
          {#each keys as key (key)}
            {@const meta = metricMeta(data.meta, key)}
            {@const f = formatValue(data.meta, key, latest?.[key])}
            {#if meta.chart}
              <button
                class="cell chartable"
                onclick={() => openTrend(`${s.id}.${key}`)}
                title="Graph {meta.label} in Trends"
              >
                <div class="lbl muted">{meta.label}</div>
                <div class="val">
                  {f.text}{#if f.unit}<span class="u">{f.unit}</span>{/if}
                  {#if f.alt}<span class="alt">{f.alt}</span>{/if}
                </div>
                <Sparkline values={seriesByAddr.get(`${s.id}.${key}`)?.values ?? []} color={meta.color} />
              </button>
            {:else}
              <div class="cell">
                <div class="lbl muted">{meta.label}</div>
                <div class="val">
                  {f.text}{#if f.unit}<span class="u">{f.unit}</span>{/if}
                  {#if f.alt}<span class="alt">{f.alt}</span>{/if}
                </div>
              </div>
            {/if}
          {/each}
        </div>
      {/each}
    </section>
  {/each}
{:else if !error}
  <p class="muted">Loading…</p>
{/if}

<style>
  .err-text {
    color: var(--err);
  }

  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 16px;
    padding: 4px 14px 14px;
  }

  .sec-head {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 0 8px;
  }
  .sec-title {
    font-weight: 600;
    font-size: 16px;
  }
  .sec-age {
    margin-left: auto;
    font-size: 12px;
  }

  .grp {
    margin: 12px 0 6px;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
    gap: 10px 12px;
  }
  .cell {
    min-width: 0;
  }
  /* Chartable cells are tap targets into Trends — reset the button, add a hover cue. */
  .cell.chartable {
    display: block;
    width: 100%;
    text-align: left;
    background: none;
    border: none;
    border-radius: 8px;
    padding: 4px;
    margin: -4px;
    color: inherit;
    font: inherit;
    cursor: pointer;
  }
  .cell.chartable:hover {
    background: color-mix(in srgb, var(--accent) 8%, transparent);
  }
  .lbl {
    font-size: 11px;
  }
  .val {
    font-size: 18px;
    font-weight: 600;
    white-space: nowrap;
  }
  .val .u {
    font-size: 12px;
    font-weight: 400;
    color: var(--text-dim);
    margin-left: 2px;
  }
  .val .alt {
    font-size: 11px;
    font-weight: 400;
    color: var(--text-dim);
    margin-left: 6px;
  }

  .dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    flex-shrink: 0;
    background: var(--text-dim);
  }
  .dot.ok {
    background: var(--ok);
  }
  .dot.warn {
    background: var(--warn);
  }
  .dot.err {
    background: var(--err);
  }
</style>
