<script lang="ts">
  import { onDestroy, onMount } from 'svelte'

  import { getBroadcastLogs, getBroadcastStatus } from '../lib/api'
  import {
    feedKey,
    formatRate,
    groupBySlot,
    snapshotUrl,
    type BroadcastLogs,
    type BroadcastStatus,
    type Feed,
    type FeedStatus,
  } from '../lib/broadcast'
  import { errMsg } from '../lib/errors'
  import { poll } from '../lib/poll.svelte'
  import { acquireWakeLock, releaseWakeLock } from '../lib/wakelock'
  import { startWhep, whepEndpoint, type WhepSession } from '../lib/whep'

  // The broadcaster's monitor wall: every feed a tile with its two independent
  // halves (ingest / egress), a codec badge, a live snapshot, and throughput —
  // so the "camera dead, egress still serving a STANDBY placeholder" state (the
  // whole point of the wall) is visible. Tap a browser-playable van tile for live
  // WHEP; the raw journal panel is the diagnostic escape hatch.
  let { feeds }: { feeds: Feed[] } = $props()

  const sf = poll<BroadcastStatus>(getBroadcastStatus, 3000, onStatus)
  let groups = $derived(groupBySlot(feeds))
  let statusByKey = $derived(
    new Map((sf.data?.feeds ?? []).map((s) => [feedKey(s.hub, s.path), s])),
  )
  let vanReachable = $derived(sf.data?.hubs.van.reachable ?? false)

  // Throughput deltas across polls (bytes are cumulative; rate = Δbytes / Δt).
  let rates = $state<Record<string, { inR: string; outR: string }>>({})
  let prev: BroadcastStatus | null = null

  function onStatus(s: BroadcastStatus): void {
    if (prev) {
      const dt = (Date.parse(s.generated_at) - Date.parse(prev.generated_at)) / 1000
      const pmap = new Map(prev.feeds.map((f) => [feedKey(f.hub, f.path), f]))
      const next: Record<string, { inR: string; outR: string }> = {}
      for (const f of s.feeds) {
        const p = pmap.get(feedKey(f.hub, f.path))
        if (p?.bytes_received != null && f.bytes_received != null) {
          next[feedKey(f.hub, f.path)] = {
            inR: formatRate(f.bytes_received - p.bytes_received, dt),
            outR: formatRate((f.bytes_sent ?? 0) - (p.bytes_sent ?? 0), dt),
          }
        }
      }
      rates = next
    }
    prev = s
  }

  function statusOf(f: Feed): FeedStatus | undefined {
    return statusByKey.get(feedKey(f.hub, f.path))
  }
  /** A feed is worth snapshotting when its van path is actually serving video. */
  function serving(st: FeedStatus | undefined): boolean {
    return !!st?.present && !!st?.ready && (st.ingest === 'live' || st.ingest === 'standby')
  }
  function snapEligible(f: Feed, st: FeedStatus | undefined): boolean {
    // Mirror the server gate: van + serving + carries video (radio is audio-only).
    return f.hub === 'van' && f.slot_group !== 'radio' && serving(st)
  }
  /** Placeholder text for a tile with no live snapshot. */
  function placeholder(f: Feed, st: FeedStatus | undefined): string {
    if (f.hub === 'cloud') return 'cloud · P3'
    if (!st?.reachable) return '—'
    if (st.present === false) return 'no path'
    if (serving(st)) return 'audio ♪' // serving but audio-only (radio)
    return 'idle'
  }
  /** Browser-decodable (H.264/Opus) van feed → WHEP live on expand; else snapshot. */
  function playable(f: Feed, st: FeedStatus | undefined): boolean {
    return f.hub === 'van' && f.browser_url != null && serving(st)
  }

  function ingestLabel(st: FeedStatus | undefined): string {
    if (!st?.reachable) return 'hub down'
    if (st.present === false) return 'no path'
    if (st.ingest === 'live') return 'live'
    if (st.ingest === 'standby') return 'STANDBY'
    return 'idle'
  }
  function ingestClass(st: FeedStatus | undefined): string {
    if (st?.ingest === 'live') return 'live'
    if (st?.ingest === 'standby') return 'standby'
    return 'idle'
  }

  // Per-tile snapshot cache-bust, self-scheduled (a slow tile refreshes less
  // often instead of being cancelled) — the Cameras grid pattern.
  const SNAP_MS = 2000
  let bust = $state<Record<string, number>>({})
  let snapOffline = $state<Record<string, boolean>>({})
  const timers: Record<string, number> = {}

  function kick(path: string): void {
    bust = { ...bust, [path]: (bust[path] ?? 0) + 1 }
  }
  function scheduleSnap(path: string): void {
    clearTimeout(timers[path])
    timers[path] = window.setTimeout(() => {
      if (!expanded && !document.hidden) kick(path)
    }, SNAP_MS)
  }
  function onSnapLoad(path: string): void {
    if (snapOffline[path]) snapOffline = { ...snapOffline, [path]: false }
    scheduleSnap(path)
  }
  function onSnapError(path: string): void {
    if (!snapOffline[path]) snapOffline = { ...snapOffline, [path]: true }
    scheduleSnap(path)
  }

  // Expand overlay: WHEP video/audio for playable van feeds, else a large snapshot.
  let expanded = $state<Feed | null>(null)
  let expandKind = $state<'whep' | 'snapshot'>('whep')
  let liveState = $state<'connecting' | 'live' | 'error'>('connecting')
  let liveErr = $state('')
  let mediaEl = $state<HTMLVideoElement>()
  let session: WhepSession | null = null

  async function expand(f: Feed): Promise<void> {
    const st = statusOf(f)
    if (playable(f, st)) {
      expanded = f
      expandKind = 'whep'
      liveState = 'connecting'
      liveErr = ''
      await acquireWakeLock()
      try {
        session = await startWhep(whepEndpoint(f.path), {
          media: f.slot_group === 'radio' ? ['audio'] : ['video'],
          onClosed: () => {
            if (liveState === 'live') {
              liveState = 'error'
              liveErr = 'Stream dropped'
            }
          },
          unreachableMessage: 'Could not reach the hub — is mediamtx running?',
        })
        if (mediaEl) {
          mediaEl.srcObject = session.stream
          await mediaEl.play().catch(() => {})
        }
        liveState = 'live'
      } catch (e) {
        liveErr = errMsg(e)
        liveState = 'error'
      }
    } else if (snapEligible(f, st)) {
      expanded = f
      expandKind = 'snapshot'
    }
  }
  function closeExpand(): void {
    session?.close()
    session = null
    if (mediaEl) mediaEl.srcObject = null
    expanded = null
    releaseWakeLock()
  }

  // Log panel (B11): the raw journal escape hatch, poll-refreshed while open.
  let logsOpen = $state(false)
  let logs = $state<BroadcastLogs | null>(null)
  let logTimer: number | undefined
  let logBox = $state<HTMLDivElement>()

  async function refreshLogs(): Promise<void> {
    try {
      logs = await getBroadcastLogs('van', 200)
      queueMicrotask(() => logBox?.scrollTo(0, logBox.scrollHeight))
    } catch {
      /* leave the last lines visible */
    }
  }
  function toggleLogs(): void {
    logsOpen = !logsOpen
    clearTimeout(logTimer)
    if (logsOpen) {
      refreshLogs()
      const loop = (): void => {
        logTimer = window.setTimeout(async () => {
          await refreshLogs()
          if (logsOpen) loop()
        }, 4000)
      }
      loop()
    }
  }

  onMount(() => () => {})
  onDestroy(() => {
    for (const id of Object.values(timers)) clearTimeout(id)
    clearTimeout(logTimer)
    closeExpand()
  })
</script>

{#if sf.error && !sf.data}
  <p class="load-error">Couldn't load status: {sf.error}</p>
{/if}

{#if sf.data && !vanReachable}
  <div class="banner">Van hub control API unreachable — service down, or off-grid.</div>
{/if}

{#each groups as g (g.key)}
  <section class="wgroup">
    <div class="grp eyebrow">{g.label}</div>
    <div class="grid">
      {#each g.feeds as f (f.hub + '/' + f.path)}
        {@const st = statusOf(f)}
        {@const key = feedKey(f.hub, f.path)}
        <button
          class="tile"
          class:danger={st?.danger}
          class:tappable={playable(f, st) || snapEligible(f, st)}
          onclick={() => expand(f)}
        >
          <div class="thumb">
            {#if snapEligible(f, st)}
              <img
                src={snapshotUrl(f.path, bust[f.path] ?? 0)}
                alt={f.label}
                onload={() => onSnapLoad(f.path)}
                onerror={() => onSnapError(f.path)}
              />
              {#if snapOffline[f.path]}<div class="ph">no image</div>{/if}
            {:else}
              <div class="ph">{placeholder(f, st)}</div>
            {/if}
            <span class="tlabel">{f.label}</span>
            <span class="thub badge hub-{f.hub}">{f.hub}</span>
            {#if st?.danger}<span class="tdanger">STANDBY on air</span>{/if}
          </div>

          <div class="tstat">
            <span class="dot {ingestClass(st)}"></span>
            <span class="ing">{ingestLabel(st)}</span>
            {#if st?.present}
              <span class="eg" class:on={st.pulling}>▸{st.readers ?? 0}</span>
              {#if st.codec && st.codec !== 'unknown'}
                <span class="cod cod-{st.codec}">{st.codec}</span>
              {/if}
              {#if rates[key]?.outR}<span class="rate">↑{rates[key].outR}</span>{/if}
            {/if}
          </div>
        </button>
      {/each}
    </div>
  </section>
{/each}

<button class="log-toggle" onclick={toggleLogs}>
  {logsOpen ? '▾' : '▸'} Van hub log (mediamtx)
</button>
{#if logsOpen}
  <div class="logbox" bind:this={logBox}>
    {#if logs && !logs.reachable}
      <div class="logline dim">journal unreadable</div>
    {:else if logs}
      {#each logs.lines as line, i (i)}<div class="logline">{line}</div>{/each}
    {:else}
      <div class="logline dim">loading…</div>
    {/if}
  </div>
{/if}

{#if expanded}
  <div class="live" role="dialog" aria-label={`${expanded.label} live`}>
    {#if expandKind === 'whep'}
      <!-- svelte-ignore a11y_media_has_caption -->
      <video bind:this={mediaEl} autoplay playsinline muted></video>
    {:else}
      <img class="live-img" src={snapshotUrl(expanded.path, bust[expanded.path] ?? 0)} alt={expanded.label} onload={() => onSnapLoad(expanded!.path)} />
    {/if}
    <div class="live-bar">
      <span class="live-title">{expanded.label}</span>
      {#if expandKind === 'whep' && liveState === 'connecting'}<span class="live-note">Connecting…</span>{/if}
      {#if expandKind === 'whep' && liveState === 'error'}<span class="live-note err">{liveErr}</span>{/if}
      {#if expandKind === 'snapshot'}<span class="live-note">Snapshot (H.265 — no browser live)</span>{/if}
      <button class="live-close" onclick={closeExpand}>Close</button>
    </div>
  </div>
{/if}

<style>
  .load-error {
    color: var(--err);
    margin: 0 0 12px;
  }
  .banner {
    background: color-mix(in srgb, var(--err) 12%, var(--surface));
    border: 1px solid var(--err);
    border-radius: 10px;
    padding: 8px 12px;
    margin-bottom: 14px;
    font-size: 13px;
  }

  .wgroup {
    margin-bottom: 18px;
  }
  .grp {
    margin: 0 0 8px;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
  }
  @media (min-width: 700px) {
    .grid {
      grid-template-columns: repeat(3, 1fr);
    }
  }
  @media (min-width: 1100px) {
    .grid {
      grid-template-columns: repeat(4, 1fr);
    }
  }

  .tile {
    display: flex;
    flex-direction: column;
    padding: 0;
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    background: var(--surface);
    text-align: left;
    cursor: default;
  }
  .tile.tappable {
    cursor: pointer;
  }
  .tile.danger {
    border-color: var(--err);
    box-shadow: 0 0 0 1px var(--err);
  }

  .thumb {
    position: relative;
    aspect-ratio: 16 / 9;
    background: #000;
  }
  .thumb img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }
  .ph {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text-dim);
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  .tlabel {
    position: absolute;
    left: 6px;
    bottom: 6px;
    padding: 1px 6px;
    border-radius: 4px;
    background: rgba(0, 0, 0, 0.6);
    color: #fff;
    font-size: 12px;
    font-weight: 600;
  }
  .thub {
    position: absolute;
    top: 6px;
    left: 6px;
    background: rgba(0, 0, 0, 0.55);
  }
  .tdanger {
    position: absolute;
    top: 6px;
    right: 6px;
    padding: 1px 6px;
    border-radius: 4px;
    background: var(--err);
    color: #fff;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
  }

  .tstat {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 8px;
    font-size: 12px;
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
    background: var(--text-dim);
  }
  .dot.live {
    background: var(--ok);
  }
  .dot.standby {
    background: var(--warn);
  }
  .dot.idle {
    background: var(--text-dim);
  }
  .ing {
    color: var(--text);
  }
  .eg {
    color: var(--text-dim);
    font-family: var(--mono, monospace);
  }
  .eg.on {
    color: var(--ok);
  }
  .cod {
    margin-left: auto;
    font-size: 10px;
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0 4px;
  }
  .cod-match {
    color: var(--ok);
    border-color: var(--ok);
  }
  .cod-mismatch {
    color: var(--err);
    border-color: var(--err);
  }
  .rate {
    color: var(--text-dim);
    font-family: var(--mono, monospace);
    font-size: 11px;
  }

  .log-toggle {
    display: block;
    width: 100%;
    text-align: left;
    padding: 8px 10px;
    margin-top: 4px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
    color: var(--text);
    font-size: 13px;
    cursor: pointer;
  }
  .logbox {
    margin-top: 6px;
    max-height: 280px;
    overflow: auto;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 10px;
  }
  .logline {
    font-family: var(--mono, monospace);
    font-size: 11px;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--text);
  }
  .logline.dim {
    color: var(--text-dim);
  }

  .live {
    position: fixed;
    inset: 0;
    z-index: 50;
    background: #000;
    display: flex;
    flex-direction: column;
  }
  .live video,
  .live-img {
    flex: 1;
    width: 100%;
    min-height: 0;
    object-fit: contain;
    background: #000;
  }
  .live-bar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0.6rem 0.9rem;
    background: var(--surface);
    border-top: 1px solid var(--border);
  }
  .live-title {
    font-weight: 600;
  }
  .live-note {
    color: var(--text-dim);
    font-size: 0.85rem;
  }
  .live-note.err {
    color: var(--err);
  }
  .live-close {
    margin-left: auto;
    padding: 0.4rem 0.9rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg);
    color: var(--text);
    cursor: pointer;
  }

  .badge {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    border-radius: 4px;
    padding: 1px 5px;
    color: #fff;
  }
  .hub-van {
    color: var(--ok);
  }
  .hub-cloud {
    color: var(--accent, #6ab);
  }
</style>
