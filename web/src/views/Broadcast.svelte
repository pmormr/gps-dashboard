<script lang="ts">
  import { onMount } from 'svelte'

  import { getBroadcastFeeds } from '../lib/api'
  import { type BroadcastFeeds } from '../lib/broadcast'
  import { errMsg } from '../lib/errors'
  import BroadcastConfig from './BroadcastConfig.svelte'
  import BroadcastWall from './BroadcastWall.svelte'

  // One tab, two surfaces: the live monitor wall (race-day glance) and the
  // grab-and-go config reference. Config is static, fetched once and shared with
  // both; the wall polls status/snapshots/logs on top of it.
  let data = $state<BroadcastFeeds | null>(null)
  let loadError = $state('')
  let mode = $state<'wall' | 'config'>('wall')

  onMount(() => {
    getBroadcastFeeds()
      .then((r) => (data = r))
      .catch((e) => (loadError = errMsg(e)))
  })
</script>

<header class="page-head">
  <h1>Broadcast</h1>
  <p class="muted">
    {#if loadError}<span class="err-text">{loadError}</span
      >{:else if mode === 'wall'}Live monitor wall — both sides of every feed{:else}Event-day feed
      config — grab-and-go send + OBS strings{/if}
  </p>
</header>

<div class="seg" role="tablist">
  <button role="tab" aria-selected={mode === 'wall'} class:on={mode === 'wall'} onclick={() => (mode = 'wall')}>
    Monitor wall
  </button>
  <button role="tab" aria-selected={mode === 'config'} class:on={mode === 'config'} onclick={() => (mode = 'config')}>
    Config
  </button>
</div>

{#if data}
  {#if mode === 'wall'}
    <BroadcastWall feeds={data.feeds} />
  {:else}
    <BroadcastConfig {data} />
  {/if}
{:else if !loadError}
  <p class="muted">Loading…</p>
{/if}

<style>
  .err-text {
    color: var(--err);
  }
  .seg {
    display: inline-flex;
    gap: 2px;
    margin-bottom: 16px;
    padding: 2px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
  }
  .seg button {
    padding: 6px 16px;
    border: none;
    border-radius: 6px;
    background: transparent;
    color: var(--text-dim);
    font-size: 13px;
    cursor: pointer;
  }
  .seg button.on {
    background: var(--bg);
    color: var(--text);
    font-weight: 600;
  }
</style>
