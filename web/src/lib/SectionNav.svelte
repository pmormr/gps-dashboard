<script lang="ts">
  // The shared secondary nav: a sticky pill strip for the current top-level tab's
  // sub-destinations. Rendered once by Shell (keyed by router.current.tab), so
  // every section sub-view gets lateral navigation with zero per-view wiring.
  import { router } from './router.svelte'
  import { SECTIONS } from './routes'

  const links = $derived(SECTIONS[router.current.tab] ?? [])
</script>

{#if links.length > 1}
  <nav class="section-nav" aria-label="Section">
    {#each links as l (l.to)}
      <button
        class="pill"
        class:active={router.path === l.to}
        onclick={() => router.navigate(l.to)}
      >
        {l.label}
      </button>
    {/each}
  </nav>
{/if}

<style>
  .section-nav {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    scrollbar-width: none;
    position: sticky;
    top: 0;
    z-index: 10;
    margin-bottom: 14px;
    padding: 4px 0;
    background: var(--bg);
  }
  .section-nav::-webkit-scrollbar {
    display: none;
  }
  .pill {
    flex: none;
    padding: 6px 14px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: var(--surface);
    color: var(--text-dim);
    font: inherit;
    font-size: 13px;
    white-space: nowrap;
    cursor: pointer;
  }
  .pill.active {
    color: var(--text);
    border-color: var(--accent);
    background: color-mix(in srgb, var(--accent) 12%, var(--surface));
  }
</style>
