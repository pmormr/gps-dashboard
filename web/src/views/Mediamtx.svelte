<script lang="ts">
  import { getMediamtx, type MediamtxPath, type MediamtxStatus } from '../lib/api'
  import { poll } from '../lib/poll.svelte'
  import StatusCheckPage from '../lib/StatusCheckPage.svelte'

  const feed = poll<MediamtxStatus>(getMediamtx, 10000)
  let data = $derived(feed.data)
  let error = $derived(feed.error)

  // Live streams (and the most-watched) first, then idle on-demand paths.
  let paths = $derived(
    [...(data?.paths ?? [])].sort(
      (a, b) => Number(b.ready) - Number(a.ready) || b.readers - a.readers || a.name.localeCompare(b.name),
    ),
  )

  const detail = (p: MediamtxPath): string => {
    const bits = [p.readers === 1 ? '1 viewer' : `${p.readers} viewers`]
    if (p.tracks.length) bits.push(p.tracks.join(', '))
    return bits.join(' · ')
  }
</script>

<StatusCheckPage title="Media" subtitle="MediaMTX hub" {error} status={data}>
  {#if data}
    <section class="panel">
      <div class="panel-title eyebrow">Hub</div>
      <div class="kv">
        <span class="k">Service</span>
        <span class="v {data.service_state === 'active' ? 'ok' : 'err'}">{data.service_state}</span>
      </div>
      <div class="kv">
        <span class="k">Control API</span>
        <span class="v {data.api_ok ? 'ok' : 'err'}">{data.api_ok ? 'reachable' : 'unreachable'}</span>
      </div>
      <div class="kv"><span class="k">Live streams</span>
        <span class="v {data.summary.ready ? 'ok' : ''}">{data.summary.ready} / {data.summary.total}</span>
      </div>
      <div class="kv"><span class="k">Viewers</span><span class="v">{data.summary.readers}</span></div>
      <div class="kv">
        <span class="k">RTSP (:8554)</span>
        <span class="v {data.listening.rtsp ? 'ok' : 'err'}">{data.listening.rtsp ? 'listening' : 'down'}</span>
      </div>
      <div class="kv">
        <span class="k">WebRTC (:8889)</span>
        <span class="v {data.listening.webrtc ? 'ok' : 'err'}"
          >{data.listening.webrtc ? 'listening' : 'down'}</span>
      </div>
    </section>

    {#if paths.length}
      <section class="panel">
        <div class="panel-title eyebrow">Streams</div>
        {#each paths as p (p.name)}
          <div class="kv">
            <span class="dot {p.ready ? 'ok' : 'idle'}"></span>
            <span class="grow">{p.name}</span>
            <span class="v {p.ready ? 'ok' : ''}">{p.ready ? detail(p) : 'idle'}</span>
          </div>
        {/each}
      </section>
    {/if}
  {/if}
</StatusCheckPage>

<style>
  /* Idle on-demand paths: a dimmed dot, since "not ready" is the healthy resting state. */
  .dot.idle {
    background: var(--text-dim);
    opacity: 0.4;
  }
</style>
