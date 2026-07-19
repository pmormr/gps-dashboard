<script lang="ts">
  import { getGpsdStatus, type GpsdStatus } from '../lib/api'
  import { fmtAltitude, fmtSpeed } from '../lib/geo'
  import { poll } from '../lib/poll.svelte'
  import StatusCheckPage from '../lib/StatusCheckPage.svelte'

  const feed = poll<GpsdStatus>(getGpsdStatus, 30000)
  let data = $derived(feed.data)
  let error = $derived(feed.error)
</script>

<StatusCheckPage title="gpsd" subtitle="GPS receiver" {error} status={data}>
  {#if data}
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
  {/if}
</StatusCheckPage>
