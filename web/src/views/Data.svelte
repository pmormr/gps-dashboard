<script lang="ts">
  import { getDataStatus, type DataChunk, type DataStatus } from '../lib/api'
  import { poll } from '../lib/poll.svelte'
  import { ageSeconds, formatAge } from '../lib/sensors'

  const feed = poll<DataStatus>(getDataStatus, 60000)
  let data = $derived(feed.data)
  let error = $derived(feed.error)

  const SECTION_LABELS: Record<string, string> = {
    places: 'Places',
    map: 'Map & terrain',
    sky: 'Sky',
    history: 'History layers',
    docs: 'Docs',
  }

  /** How a non-runnable chunk updates — shown as a small tag until Phase 2 buttons land. */
  const ACTION_TAGS: Record<string, string> = {
    staged_file: 'staged file',
    recipe_only: 'recipe',
    readonly: 'automatic',
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
    return parts.join(' · ')
  }

  function ageText(c: DataChunk): string {
    if (c.state === 'error') return 'probe error'
    if (c.synced_at == null) return 'never'
    return formatAge(ageSeconds(c.synced_at))
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
              {#if ACTION_TAGS[c.action]}<span class="tag">{ACTION_TAGS[c.action]}</span>{/if}
              <span class="age muted">{ageText(c)}</span>
            </div>
            <div class="meta muted">
              {[c.cadence, c.transfer, detailText(c)].filter(Boolean).join(' · ')}
            </div>
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

  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 16px;
    padding: 4px 14px 10px;
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

  .meta {
    font-size: 12px;
    margin-top: 2px;
  }

  .warning {
    font-size: 12px;
    color: var(--warn);
    margin-top: 4px;
  }
</style>
