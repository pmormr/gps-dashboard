<script lang="ts">
  import { getNtp, type NtpStatus } from '../lib/api'
  import { poll } from '../lib/poll.svelte'

  const feed = poll<NtpStatus>(getNtp, 30000)
  let data = $derived(feed.data)
  let error = $derived(feed.error)

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
  <div class="status-banner {data.overall_ok ? 'ok' : 'err'}">
    {data.overall_ok ? '✓ All checks passing' : '✗ One or more checks failing'}
  </div>

  <section class="panel">
    <div class="panel-title eyebrow">Checks</div>
    {#each data.checks as c (c.name)}
      <div class="row">
        <span class="dot {c.ok ? 'ok' : 'err'}"></span>
        <span class="grow">{c.name}</span>
        <span class="tag {c.ok ? 'ok' : 'err'}">{c.ok ? 'PASS' : 'FAIL'}</span>
      </div>
    {/each}
  </section>

  <section class="panel">
    <div class="panel-title eyebrow">Sync status</div>
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
      <div class="panel-title eyebrow">Sources</div>
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
    <div class="panel-title eyebrow">Mode</div>
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

  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 16px;
    overflow: hidden;
  }
  .panel-title {
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
