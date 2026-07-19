<script lang="ts">
  import { getNtp, type NtpStatus } from '../lib/api'
  import { poll } from '../lib/poll.svelte'
  import StatusCheckPage from '../lib/StatusCheckPage.svelte'

  const feed = poll<NtpStatus>(getNtp, 30000)
  let data = $derived(feed.data)
  let error = $derived(feed.error)

  const offset = (d: NtpStatus): string =>
    d.tracking.offset_ms == null
      ? '—'
      : `${d.tracking.offset_ms.toFixed(3)} ms ${d.tracking.offset_dir ?? ''}`.trim()
</script>

<StatusCheckPage title="NTP" subtitle="Time sync / chrony" {error} status={data}>
  {#if data}
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
  {/if}
</StatusCheckPage>

<style>
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
