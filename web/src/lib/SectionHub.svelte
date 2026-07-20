<script lang="ts">
  // The section-hub launcher: a grid of destination tiles, each with a live
  // one-line status. Replaces the old buried Systems button list — a tap target
  // per destination, glanceable, and it scales as more sub-tools land.
  import { router } from './router.svelte'
  import type { HubTile } from './routes'

  let { tiles }: { tiles: HubTile[] } = $props()
</script>

<div class="hub">
  {#each tiles as t (t.to)}
    <button class="tile" onclick={() => router.navigate(t.to)}>
      <span class="tile-head">
        {#if t.dot}<span class="dot {t.dot}"></span>{/if}
        <span class="tile-label">{t.label}</span>
      </span>
      {#if t.sub}<span class="tile-sub muted">{t.sub}</span>{/if}
    </button>
  {/each}
</div>

<style>
  .hub {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(min(160px, 100%), 1fr));
    gap: 12px;
  }
  .tile {
    display: flex;
    flex-direction: column;
    gap: 6px;
    align-items: flex-start;
    text-align: left;
    padding: 16px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    color: var(--text);
    font: inherit;
    cursor: pointer;
  }
  .tile:hover {
    border-color: var(--accent);
  }
  .tile-head {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .tile-label {
    font-size: 16px;
    font-weight: 600;
  }
  .tile-sub {
    font-size: 12px;
  }
  .dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    flex-shrink: 0;
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
</style>
