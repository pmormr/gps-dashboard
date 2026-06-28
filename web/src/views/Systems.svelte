<script lang="ts">
  import { onDestroy, onMount } from 'svelte'

  import { getSensors, type SensorsResponse } from '../lib/api'
  import { router } from '../lib/router.svelte'
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

  let data = $state<SensorsResponse | null>(null)
  let error = $state<string | null>(null)
  let timer: number | undefined

  async function refresh(): Promise<void> {
    try {
      data = await getSensors()
      error = null
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  }

  onMount(() => {
    refresh()
    timer = window.setInterval(refresh, 30000)
  })
  onDestroy(() => {
    if (timer) clearInterval(timer)
  })

  const sensors = $derived(data ? orderedSensors(data.sensors) : [])
</script>

<header class="page-head">
  <h1>Systems</h1>
  <p class="muted">
    {#if error}<span class="err-text">{error}</span>{:else}House power · van · environment{/if}
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
          <div class="grp">{groupLabel(group)}</div>
        {/if}
        <div class="grid">
          {#each keys as key (key)}
            {@const f = formatValue(data.meta, key, latest?.[key])}
            <div class="cell">
              <div class="lbl muted">{metricMeta(data.meta, key).label}</div>
              <div class="val">
                {f.text}{#if f.unit}<span class="u">{f.unit}</span>{/if}
                {#if f.alt}<span class="alt">{f.alt}</span>{/if}
              </div>
            </div>
          {/each}
        </div>
      {/each}
    </section>
  {/each}

  <section class="panel">
    <div class="grp">Diagnostics</div>
    <button class="diag" onclick={() => router.navigate('/ntp')}>
      <span>NTP</span><span class="muted">time sync / chrony</span>
    </button>
    <a class="diag" href="/gpsd"><span>gpsd</span><span class="muted">GPS receiver status</span></a>
    <a class="diag" href="/sensors">
      <span>History &amp; charts</span><span class="muted">detailed sensor viewer</span>
    </a>
  </section>
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
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-dim);
    margin: 12px 0 6px;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(104px, 1fr));
    gap: 10px 12px;
  }
  .cell {
    min-width: 0;
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

  .diag {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
    width: 100%;
    padding: 11px 0;
    border-top: 1px solid var(--border);
    background: none;
    border-left: none;
    border-right: none;
    border-bottom: none;
    color: var(--accent);
    font: inherit;
    text-align: left;
    text-decoration: none;
    cursor: pointer;
  }
  .diag .muted {
    font-size: 12px;
  }
</style>
