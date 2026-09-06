<script lang="ts">
  import {
    cancelDataRun,
    getDataRun,
    getDataStatus,
    startDataUpdate,
    type DataChunk,
    type DataRun,
    type DataStatus,
  } from '../lib/api'
  import { errMsg } from '../lib/errors'
  import { poll } from '../lib/poll.svelte'
  import { ageSeconds, formatAge } from '../lib/sensors'

  const feed = poll<DataStatus>(getDataStatus, 60000)
  let data = $derived(feed.data)
  let error = $derived(feed.error)

  /** Error from the last start/cancel action (distinct from the feed's). */
  let actionError = $state<string | null>(null)

  /** The run the log panel follows: the in-flight run, or a finished one under review. */
  let watchId = $state<number | null>(null)
  let watch = $state<{ run: DataRun; log: string } | null>(null)

  // Follow the watched run: poll its log every 2 s while running, then refresh
  // the freshness feed once and hold the final state for review.
  $effect(() => {
    const id = watchId
    if (id == null) {
      watch = null
      return
    }
    let stopped = false
    let timer: number | undefined
    async function tick(runId: number): Promise<void> {
      try {
        const res = await getDataRun(runId)
        if (stopped) return
        const prev = watch
        watch = res
        if (res.run.status === 'running') timer = window.setTimeout(() => tick(runId), 2000)
        else if (!prev || prev.run.status === 'running') feed.refresh()
      } catch {
        if (!stopped) timer = window.setTimeout(() => tick(runId), 5000)
      }
    }
    tick(id)
    return () => {
      stopped = true
      if (timer !== undefined) clearTimeout(timer)
    }
  })

  // A run started elsewhere (SSH, another tab) shows up in the status feed —
  // follow it automatically when nothing else is being watched.
  $effect(() => {
    const active = data?.active_run
    if (active && watchId == null) watchId = active.id
  })

  let busy = $derived(data?.active_run != null || watch?.run.status === 'running')

  async function start(chunk: DataChunk): Promise<void> {
    actionError = null
    try {
      const res = await startDataUpdate(chunk.id)
      watchId = res.run.id
    } catch (e) {
      actionError = errMsg(e)
    }
    feed.refresh()
  }

  async function cancel(run: DataRun): Promise<void> {
    actionError = null
    try {
      await cancelDataRun(run.id)
    } catch (e) {
      actionError = errMsg(e)
    }
  }

  const SECTION_LABELS: Record<string, string> = {
    places: 'Places',
    map: 'Map & terrain',
    sky: 'Sky',
    history: 'History layers',
    docs: 'Docs',
  }

  /** Tag text for a chunk with no update button. */
  function tagText(c: DataChunk): string | null {
    if (c.run.requires_staged && !c.run.runnable) return 'no staged file'
    if (c.action === 'recipe_only') return 'recipe'
    if (c.action === 'readonly') return 'automatic'
    return null
  }

  /** Group chunks by section, preserving registry order. */
  function grouped(chunks: DataChunk[]): [string, DataChunk[]][] {
    const out: [string, DataChunk[]][] = []
    for (const c of chunks) {
      const last = out[out.length - 1]
      if (last && last[0] === c.section) last[1].push(c)
      else out.push([c.section, [c]])
    }
    return out
  }

  function dotClass(state: DataChunk['state']): string {
    if (state === 'ok') return 'ok'
    if (state === 'stale') return 'warn'
    return 'err'
  }

  function runDotClass(status: DataRun['status']): string {
    if (status === 'running') return 'run'
    if (status === 'ok') return 'ok'
    return 'err'
  }

  function formatSize(bytes: number): string {
    if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
    if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`
    if (bytes >= 1e3) return `${(bytes / 1e3).toFixed(1)} kB`
    return `${bytes} B`
  }

  /** Compact probe facts: row/tile counts and on-disk size. */
  function detailText(c: DataChunk): string {
    const d = c.detail
    const parts: string[] = []
    if (typeof d.rows === 'number' && d.rows > 0) parts.push(`${d.rows.toLocaleString()} rows`)
    if (typeof d.paths === 'number' && d.paths > 0) parts.push(`${d.paths.toLocaleString()} paths`)
    if (typeof d.flights === 'number' && d.flights > 0)
      parts.push(`${d.flights.toLocaleString()} flights`)
    if (typeof d.tiles === 'number' && d.tiles > 0)
      parts.push(`${d.tiles.toLocaleString()} tiles`)
    if (typeof d.size_bytes === 'number') parts.push(formatSize(d.size_bytes))
    if (c.run.staged) parts.push(`staged: ${formatSize(c.run.staged.size_bytes)}`)
    return parts.join(' · ')
  }

  function ageText(c: DataChunk): string {
    if (c.state === 'error') return 'probe error'
    if (c.synced_at == null) return 'never'
    return formatAge(ageSeconds(c.synced_at))
  }

  /** "last run failed · 2 h" summary for a chunk's most recent run. */
  function lastRunText(run: DataRun): string {
    const when = run.finished ?? run.started
    return `last run ${run.status} · ${formatAge(ageSeconds(when))}`
  }

  function labelFor(chunkId: string): string {
    return data?.chunks.find((c) => c.id === chunkId)?.label ?? chunkId
  }

  const summary = $derived.by(() => {
    if (!data) return ''
    const bad = data.chunks.filter((c) => c.state !== 'ok').length
    const warned = data.chunks.filter((c) => c.warnings.length > 0).length
    if (bad === 0 && warned === 0) return 'All chunks fresh — ready to go dark'
    const parts: string[] = []
    if (bad > 0) parts.push(`${bad} chunk${bad === 1 ? '' : 's'} need attention`)
    if (warned > 0) parts.push(`${warned} ordering warning${warned === 1 ? '' : 's'}`)
    return parts.join(' · ')
  })
</script>

<header class="page-head">
  <h1>Offline data</h1>
  <p class="muted">
    {#if error}<span class="err-text">{error}</span>{:else}{summary ||
        'Chunk freshness — am I ready to go dark?'}{/if}
  </p>
</header>

{#if actionError}
  <p class="action-error">{actionError}</p>
{/if}

{#if watch}
  <section class="panel run-panel">
    <div class="run-head">
      <span class="dot {runDotClass(watch.run.status)}"></span>
      <span class="name">{labelFor(watch.run.chunk)}</span>
      <span class="run-status">{watch.run.status}</span>
      <span class="age muted">started {formatAge(ageSeconds(watch.run.started))}</span>
      {#if watch.run.status === 'running'}
        <button onclick={() => watch && cancel(watch.run)}>Cancel</button>
      {:else}
        <button onclick={() => (watchId = null)}>Dismiss</button>
      {/if}
    </div>
    <pre class="log">{watch.log || '(no output yet)'}</pre>
  </section>
{/if}

{#if data}
  {#each grouped(data.chunks) as [section, chunks] (section)}
    <section class="panel">
      <div class="grp eyebrow">{SECTION_LABELS[section] ?? section}</div>
      {#each chunks as c (c.id)}
        <div class="row">
          <span class="dot {dotClass(c.state)}"></span>
          <div class="body">
            <div class="head">
              <span class="name">{c.label}</span>
              {#if tagText(c)}<span class="tag">{tagText(c)}</span>{/if}
              <span class="age muted">{ageText(c)}</span>
              {#if c.run.runnable}
                <button
                  class="update"
                  disabled={busy}
                  onclick={() => start(c)}
                >
                  {c.run.requires_staged ? 'Import' : 'Update'}
                </button>
              {/if}
            </div>
            <div class="meta muted">
              {[c.cadence, c.transfer, detailText(c)].filter(Boolean).join(' · ')}
            </div>
            {#if c.last_run}
              <button
                class="last-run {c.last_run.status}"
                onclick={() => c.last_run && (watchId = c.last_run.id)}
              >
                {lastRunText(c.last_run)}
              </button>
            {/if}
            {#each c.warnings as warning (warning)}
              <div class="warning">{warning}</div>
            {/each}
            {#if c.error}
              <div class="warning">{c.error}</div>
            {/if}
          </div>
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

  .action-error {
    color: var(--err);
    font-size: 13px;
    margin-bottom: 12px;
  }

  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 16px;
    padding: 4px 14px 10px;
  }

  .run-panel {
    padding: 12px 14px;
  }

  .run-head {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .run-status {
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-dim);
  }

  .run-head button {
    margin-left: auto;
    background: var(--surface-2);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 6px;
    padding: 5px 12px;
    font: inherit;
    font-size: 13px;
    cursor: pointer;
  }

  .log {
    margin: 10px 0 0;
    padding: 10px;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 8px;
    font-size: 11px;
    line-height: 1.5;
    max-height: 240px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .grp {
    margin: 12px 0 2px;
  }

  .row {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 10px 0;
    border-top: 1px solid var(--border);
  }
  .row:first-of-type {
    border-top: none;
  }

  .dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    flex-shrink: 0;
    margin-top: 5px;
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
  .dot.run {
    background: var(--accent);
  }

  .body {
    flex: 1;
    min-width: 0;
  }

  .head {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  .name {
    font-weight: 600;
  }
  .tag {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-dim);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 1px 5px;
  }
  .age {
    margin-left: auto;
    font-size: 12px;
    white-space: nowrap;
  }

  .update {
    background: var(--surface-2);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 6px;
    padding: 4px 12px;
    font: inherit;
    font-size: 13px;
    cursor: pointer;
    flex-shrink: 0;
  }
  .update:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .meta {
    font-size: 12px;
    margin-top: 2px;
  }

  .last-run {
    display: inline-block;
    background: none;
    border: none;
    padding: 0;
    margin-top: 4px;
    font: inherit;
    font-size: 12px;
    color: var(--text-dim);
    cursor: pointer;
    text-decoration: underline;
    text-decoration-style: dotted;
    text-underline-offset: 3px;
  }
  .last-run.failed,
  .last-run.cancelled {
    color: var(--warn);
  }

  .warning {
    font-size: 12px;
    color: var(--warn);
    margin-top: 4px;
  }
</style>
