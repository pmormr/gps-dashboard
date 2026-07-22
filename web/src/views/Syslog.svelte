<script lang="ts">
  import { getSyslog, type SyslogStatus } from '../lib/api'
  import { poll } from '../lib/poll.svelte'
  import StatusCheckPage from '../lib/StatusCheckPage.svelte'

  const feed = poll<SyslogStatus>(getSyslog, 15000)
  let data = $derived(feed.data)
  let error = $derived(feed.error)

  const int = (n: number | null | undefined): string => (n == null ? '—' : n.toLocaleString())

  // Buffering off-grid is healthy: queued>0 just means "holding until Graylog is
  // reachable again". Only dropped>0 is a real loss.
  function buffer(s: SyslogStatus['stats']): { text: string; cls: string } {
    if (!s || s.queued == null) return { text: 'unavailable', cls: '' }
    if (s.queued === 0) return { text: 'drained · online', cls: 'ok' }
    return { text: `buffering ${s.queued.toLocaleString()} msgs · offline`, cls: 'warn' }
  }
</script>

<StatusCheckPage title="Logs" subtitle="syslog relay → Graylog" {error} status={data}>
  {#if data}
    <section class="panel">
      <div class="panel-title eyebrow">Forwarding</div>
      <div class="kv">
        <span class="k">Service</span>
        <span class="v {data.service_state === 'active' ? 'ok' : 'err'}">{data.service_state}</span>
      </div>
      <div class="kv"><span class="k">Destination</span><span class="v">{data.destination}</span></div>
      <div class="kv">
        <span class="k">Buffer</span>
        <span class="v {buffer(data.stats).cls}">{buffer(data.stats).text}</span>
      </div>
      <div class="kv"><span class="k">Delivered</span><span class="v">{int(data.stats?.written)}</span></div>
      <div class="kv">
        <span class="k">Dropped</span>
        <span class="v {data.stats?.dropped ? 'err' : 'ok'}">{int(data.stats?.dropped)}</span>
      </div>
      <div class="kv">
        <span class="k">Throughput</span>
        <span class="v">{data.stats?.eps_1h ?? '—'} / s (1h)</span>
      </div>
    </section>

    <section class="panel">
      <div class="panel-title eyebrow">Relay</div>
      <div class="kv">
        <span class="k">Listening (UDP :514)</span>
        <span class="v {data.listening.udp ? 'ok' : 'err'}">{data.listening.udp ? 'yes' : 'no'}</span>
      </div>
      <div class="kv">
        <span class="k">Listening (TCP :514)</span>
        <span class="v {data.listening.tcp ? 'ok' : 'err'}">{data.listening.tcp ? 'yes' : 'no'}</span>
      </div>
      <div class="kv">
        <span class="k">Relayed from devices</span><span class="v">{int(data.stats?.relayed)}</span>
      </div>
      <div class="kv"><span class="k">Local (this Pi)</span><span class="v">{int(data.stats?.local)}</span></div>
    </section>

    {#if !data.stats}
      <p class="muted">Buffer stats unavailable — <code>syslog-ng-ctl</code> not readable.</p>
    {/if}
  {/if}
</StatusCheckPage>
