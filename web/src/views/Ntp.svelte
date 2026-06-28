<script lang="ts">
  import { onDestroy, onMount } from 'svelte'

  import { getNtp, type NtpStatus } from '../lib/api'

  let data = $state<NtpStatus | null>(null)
  let error = $state<string | null>(null)
  let timer: number | undefined

  async function refresh(): Promise<void> {
    try {
      data = await getNtp()
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

  const offset = (d: NtpStatus): string =>
    d.tracking.offset_ms == null
      ? '—'
      : `${d.tracking.offset_ms.toFixed(3)} ms ${d.tracking.offset_dir ?? ''}`.trim()
</script>

<header class="page-head">
  <h1>NTP</h1>
  <p class="muted">
    Time sync / chrony{#if error} — <span class="err-text">{error}</span>{/if}
  </p>
</header>

{#if data}
  <div class="banner {data.overall_ok ? 'ok' : 'err'}">
    {data.overall_ok ? '✓ All checks passing' : '✗ One or more checks failing'}
  </div>

  <section class="panel">
    <div class="panel-title">Checks</div>
    {#each data.checks as c (c.name)}
      <div class="row">
        <span class="dot {c.ok ? 'ok' : 'err'}"></span>
        <span class="grow">{c.name}</span>
        <span class="tag {c.ok ? 'ok' : 'err'}">{c.ok ? 'PASS' : 'FAIL'}</span>
      </div>
    {/each}
  </section>

  <section class="panel">
    <div class="panel-title">Sync status</div>
    <div class="kv"><span class="k">Service</span>
      <span class="v {data.service_state === 'active' ? 'ok' : 'err'}">{data.service_state}</span>
    </div>
    <div class="kv"><span class="k">Reference</span>
      <span class="v {data.tracking.synced ? 'ok' : 'err'}"
        >{data.tracking.reference ?? (data.tracking.synced ? '—' : 'Not synchronised')}</span>
    </div>
    <div class="kv"><span class="k">Stratum</span><span class="v">{data.tracking.stratum ?? '—'}</span></div>
    <div class="kv"><span class="k">Offset</span><span class="v">{offset(data)}</span></div>
    <div class="kv"><span class="k">LAN NTP server</span>
      <span class="v {data.serving ? 'ok' : 'err'}"
        >{data.serving ? 'active (port 123)' : 'not listening'}</span>
    </div>
  </section>

  {#if data.sources.length}
    <section class="panel">
      <div class="panel-title">Sources</div>
      {#each data.sources as src (src.name)}
        <div class="row mono">
          <span class="sel">{src.selected ? '*' : ''}</span>
          <span class="grow">{src.name} <span class="muted">stratum {src.stratum}</span></span>
          <span class="muted">{src.sample}</span>
        </div>
      {/each}
    </section>
  {/if}

  <section class="panel">
    <div class="panel-title">Mode</div>
    <div class="kv"><span class="k">GPS source</span>
      <span class="v {data.gps_source ? 'ok' : 'err'}">{data.gps_source ? 'configured' : 'not found'}</span>
    </div>
    <div class="kv"><span class="k">PPS source</span>
      <span class="v {data.pps_source?.selected ? 'ok' : data.pps_source ? 'warn' : ''}">
        {data.pps_source?.selected
          ? 'active'
          : data.pps_source
            ? 'present, not selected'
            : 'not configured'}
      </span>
    </div>
    <div class="kv"><span class="k">Expected accuracy</span>
      <span class="v">{data.pps_source?.selected ? '~1 µs (PPS)' : '~100 ms (GPS only)'}</span>
    </div>
  </section>
{:else if !error}
  <p class="muted">Loading…</p>
{/if}

<style>
  .err-text {
    color: var(--err);
  }

  .banner {
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 16px;
    font-weight: 600;
  }
  .banner.ok {
    background: rgba(62, 207, 142, 0.12);
    color: var(--ok);
    border: 1px solid rgba(62, 207, 142, 0.4);
  }
  .banner.err {
    background: rgba(239, 83, 80, 0.12);
    color: var(--err);
    border: 1px solid rgba(239, 83, 80, 0.4);
  }

  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 16px;
    overflow: hidden;
  }
  .panel-title {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-dim);
    padding: 12px 14px 4px;
  }

  .row,
  .kv {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 9px 14px;
    border-top: 1px solid var(--border);
  }
  .panel-title + .row,
  .panel-title + .kv {
    border-top: none;
  }

  .grow {
    flex: 1;
  }
  .kv .k {
    color: var(--text-dim);
  }
  .kv .v {
    margin-left: auto;
    text-align: right;
    font-weight: 500;
    word-break: break-all;
  }
  .v.ok {
    color: var(--ok);
  }
  .v.err {
    color: var(--err);
  }
  .v.warn {
    color: var(--warn);
  }

  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .dot.ok {
    background: var(--ok);
  }
  .dot.err {
    background: var(--err);
  }

  .tag {
    font-size: 12px;
    font-weight: 600;
  }
  .tag.ok {
    color: var(--ok);
  }
  .tag.err {
    color: var(--err);
  }

  .mono {
    font-family: ui-monospace, monospace;
    font-size: 13px;
  }
  .sel {
    width: 10px;
    color: var(--ok);
    font-weight: 700;
  }
</style>
