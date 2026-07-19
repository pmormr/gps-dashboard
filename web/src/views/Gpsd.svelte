<script lang="ts">
  import { getGpsdStatus, type GpsdStatus } from '../lib/api'
  import { fmtAltitude, fmtSpeed } from '../lib/geo'
  import { poll } from '../lib/poll.svelte'

  const feed = poll<GpsdStatus>(getGpsdStatus, 30000)
  let data = $derived(feed.data)
  let error = $derived(feed.error)
</script>

<header class="page-head">
  <h1>gpsd</h1>
  <p class="muted">
    GPS receiver{#if error} — <span class="err-text">{error}</span>{/if}
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
    <div class="panel-title eyebrow">GPS details</div>
    <div class="kv"><span class="k">Service</span>
      <span class="v {data.service_state === 'active' ? 'ok' : 'err'}">{data.service_state}</span>
    </div>
    <div class="kv"><span class="k">Device</span>
      <span class="v {data.device_present ? 'ok' : 'err'}">{data.device}</span>
    </div>
    <div class="kv"><span class="k">Fix mode</span>
      <span class="v {data.fix_mode >= 2 ? 'ok' : 'err'}">{data.fix_label}</span>
    </div>
    <div class="kv"><span class="k">Satellites</span>
      <span class="v {data.sats_used >= 4 ? 'ok' : data.sats_used > 0 ? 'warn' : 'err'}"
        >{data.sats_used} used / {data.sats_visible} visible</span>
    </div>
    <div class="kv"><span class="k">Position</span>
      <span class="v {data.frozen ? 'err' : 'ok'}">{data.frozen ? 'FROZEN' : 'moving'}</span>
    </div>
  </section>

  <section class="panel">
    <div class="panel-title eyebrow">Latest logged point</div>
    {#if data.latest}
      <div class="kv"><span class="k">Timestamp</span><span class="v">{data.latest.timestamp}</span></div>
      <div class="kv"><span class="k">Coordinates</span>
        <span class="v">{data.latest.lat.toFixed(5)}, {data.latest.lon.toFixed(5)}</span>
      </div>
      <div class="kv"><span class="k">Speed</span><span class="v">{fmtSpeed(data.latest.speed)}</span></div>
      <div class="kv"><span class="k">Altitude</span><span class="v">{fmtAltitude(data.latest.altitude)}</span></div>
      <div class="kv"><span class="k">Data age</span>
        <span class="v {data.data_age != null && data.data_age < 30 ? 'ok' : 'err'}"
          >{data.data_age != null ? `${data.data_age}s` : '—'}</span>
      </div>
    {:else}
      <div class="kv"><span class="muted">No GPS points logged yet.</span></div>
    {/if}
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
</style>
