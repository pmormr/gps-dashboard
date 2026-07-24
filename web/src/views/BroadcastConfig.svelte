<script lang="ts">
  import { onDestroy } from 'svelte'

  import { copyText, groupBySlot, type BroadcastFeeds } from '../lib/broadcast'

  // Grab-and-go config reference: every feed's copy-ready send + OBS strings,
  // grouped by slot. Config is static (a prop from the tab shell); this view owns
  // only the per-button copy feedback.
  let { data }: { data: BroadcastFeeds } = $props()

  let copiedKey = $state<string | null>(null)
  let copyTimer: number | undefined
  let groups = $derived(groupBySlot(data.feeds))

  onDestroy(() => clearTimeout(copyTimer))

  async function doCopy(key: string, text: string): Promise<void> {
    const ok = await copyText(text)
    copiedKey = ok ? key : `${key}:err`
    clearTimeout(copyTimer)
    copyTimer = window.setTimeout(() => (copiedKey = null), 1500)
  }
</script>

{#snippet copyRow(rowKey: string, label: string, value: string)}
  <div class="crow">
    <span class="cr-label">{label}</span>
    <code class="cr-value">{value}</code>
    <button
      class="cr-btn"
      class:done={copiedKey === rowKey}
      class:failed={copiedKey === `${rowKey}:err`}
      onclick={() => doCopy(rowKey, value)}
    >
      {#if copiedKey === rowKey}Copied{:else if copiedKey === `${rowKey}:err`}Failed{:else}Copy{/if}
    </button>
  </div>
{/snippet}

{#snippet field(label: string, value: string)}
  <div class="fld">
    <span class="fld-label">{label}</span>
    <span class="fld-value">{value}</span>
  </div>
{/snippet}

{#if data.missing_secrets.length}
  <div class="banner">
    Cloud secrets not loaded ({data.missing_secrets.length}) — set
    <code>/etc/default/gps-broadcast</code> on the Pi. Van feeds work without it.
  </div>
{/if}

{#each groups as g (g.key)}
  <section class="panel">
    <div class="grp eyebrow">{g.label}</div>
    {#each g.feeds as f (f.hub + '/' + f.path)}
      {@const id = `${f.hub}/${f.path}`}
      <article class="feed">
        <div class="feed-head">
          <span class="name">{f.label}</span>
          <span class="badge hub-{f.hub}">{f.hub}</span>
          {#if f.standby}<span class="badge standby">standby</span>{/if}
          {#if f.role === 'proxy'}<span class="badge">on-demand</span>{/if}
          <span class="path">{f.path}</span>
        </div>

        {#if f.send}
          {#if f.send.single_url}
            {@render copyRow(`${id}:url`, 'Send (single URL)', f.send.single_url)}
          {/if}
          <div class="fields">
            {@render field('Host', f.send.host)}
            {@render field('Port', String(f.send.port))}
            {#if f.send.latency_ms != null}
              {@render field('Latency', `${f.send.latency_ms} ms`)}
            {/if}
            {#if f.send.encryption}{@render field('Encryption', f.send.encryption)}{/if}
          </div>
          {#if f.send.streamid}
            {@render copyRow(`${id}:sid`, 'Stream ID', f.send.streamid)}
          {/if}
          {#if f.send.passphrase}
            {@render copyRow(`${id}:pass`, 'Passphrase', f.send.passphrase)}
          {/if}
        {/if}

        {#if f.obs_read}
          {@render copyRow(`${id}:obs`, 'OBS read (RTSP +tcp)', f.obs_read)}
        {/if}

        <div class="tail">
          {#if f.expected_tracks.length}
            <span class="pins">
              {#each f.expected_tracks as t (t)}<span class="pin">{t}</span>{/each}
            </span>
          {/if}
          {#if f.browser_url}
            <a class="browser" href={f.browser_url} target="_blank" rel="noreferrer"
              >Browser preview ↗</a
            >
          {/if}
        </div>

        {#each f.notes as note (note)}<p class="note">{note}</p>{/each}
      </article>
    {/each}
  </section>
{/each}

<style>
  .banner {
    background: color-mix(in srgb, var(--warn) 14%, var(--surface));
    border: 1px solid var(--warn);
    border-radius: 10px;
    padding: 10px 14px;
    margin-bottom: 16px;
    font-size: 13px;
  }
  .banner code {
    font-size: 12px;
  }

  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 16px;
    padding: 4px 14px 12px;
  }
  .grp {
    margin: 12px 0 4px;
  }

  .feed {
    padding: 12px 0;
    border-top: 1px solid var(--border);
  }
  .feed:first-of-type {
    border-top: none;
  }

  .feed-head {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 8px;
  }
  .name {
    font-weight: 600;
  }
  .path {
    margin-left: auto;
    font-family: var(--mono, monospace);
    font-size: 12px;
    color: var(--text-dim);
  }
  .badge {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 1px 5px;
    color: var(--text-dim);
  }
  .badge.hub-van {
    border-color: var(--ok);
    color: var(--ok);
  }
  .badge.hub-cloud {
    border-color: var(--accent, #6ab);
    color: var(--accent, #6ab);
  }
  .badge.standby {
    border-color: var(--warn);
    color: var(--warn);
  }

  .crow {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 8px;
  }
  .cr-label {
    flex: 0 0 8.5rem;
    font-size: 12px;
    color: var(--text-dim);
  }
  .cr-value {
    flex: 1;
    min-width: 0;
    overflow-x: auto;
    white-space: nowrap;
    font-family: var(--mono, monospace);
    font-size: 12px;
    padding: 5px 8px;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
  }
  .cr-btn {
    flex: 0 0 auto;
    padding: 5px 12px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg);
    color: var(--text);
    font-size: 12px;
    cursor: pointer;
  }
  .cr-btn.done {
    border-color: var(--ok);
    color: var(--ok);
  }
  .cr-btn.failed {
    border-color: var(--err);
    color: var(--err);
  }

  .fields {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 18px;
    margin-top: 8px;
  }
  .fld {
    display: flex;
    gap: 6px;
    align-items: baseline;
  }
  .fld-label {
    font-size: 12px;
    color: var(--text-dim);
  }
  .fld-value {
    font-family: var(--mono, monospace);
    font-size: 12px;
  }

  .tail {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 10px;
    flex-wrap: wrap;
  }
  .pins {
    display: inline-flex;
    gap: 5px;
  }
  .pin {
    font-size: 10px;
    font-family: var(--mono, monospace);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 1px 5px;
    color: var(--text-dim);
  }
  .browser {
    font-size: 12px;
    color: var(--accent, #6ab);
  }

  .note {
    font-size: 12px;
    color: var(--text-dim);
    margin: 6px 0 0;
    line-height: 1.4;
  }

  @media (min-width: 900px) {
    .banner,
    .panel {
      max-width: 900px;
    }
  }
</style>
