<script lang="ts">
  import type { Snippet } from 'svelte'

  import { router } from './router.svelte'
  import { NAV } from './routes'

  let { children }: { children: Snippet } = $props()
</script>

<div class="shell">
  <nav class="nav">
    <div class="brand">Van OS</div>
    <ul>
      {#each NAV as item (item.label)}
        <li>
          <button
            class="navitem"
            class:active={router.current.tab === item.to}
            onclick={() => router.navigate(item.to)}
          >
            <span class="icon">{item.icon}</span>
            <span class="label">{item.label}</span>
          </button>
        </li>
      {/each}
    </ul>
  </nav>

  <main class="content">
    {@render children()}
  </main>
</div>

<style>
  .content {
    padding: 16px;
    padding-bottom: calc(var(--nav-h) + env(safe-area-inset-bottom) + 16px);
  }

  .nav {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    height: calc(var(--nav-h) + env(safe-area-inset-bottom));
    padding-bottom: env(safe-area-inset-bottom);
    background: var(--surface);
    border-top: 1px solid var(--border);
    z-index: 100;
  }

  .brand {
    display: none;
  }

  .nav ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    height: var(--nav-h);
  }

  .nav li {
    flex: 1;
    /* Flex items default to min-width:auto — a wide label ("Places") would
       refuse to shrink and push the last tab off-screen on phones. */
    min-width: 0;
  }

  .navitem {
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    background: none;
    border: none;
    color: var(--text-dim);
    font: inherit;
    font-size: 11px;
    text-decoration: none;
    cursor: pointer;
  }

  .navitem .icon {
    font-size: 20px;
    line-height: 1;
  }

  .navitem .label {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .navitem.active {
    color: var(--accent);
  }

  @media (min-width: 768px) {
    .shell {
      display: flex;
      min-height: 100svh;
    }

    .nav {
      position: sticky;
      top: 0;
      width: var(--sidebar-w);
      height: 100svh;
      flex-direction: column;
      border-top: none;
      border-right: 1px solid var(--border);
      padding-bottom: 0;
    }

    .brand {
      display: block;
      padding: 18px 20px;
      font-weight: 600;
      font-size: 18px;
      color: var(--text);
      letter-spacing: 0.5px;
    }

    .nav ul {
      flex-direction: column;
      height: auto;
    }

    .nav li {
      flex: none;
    }

    .navitem {
      flex-direction: row;
      justify-content: flex-start;
      gap: 12px;
      padding: 12px 20px;
      height: auto;
      font-size: 15px;
    }

    .navitem .icon {
      font-size: 18px;
    }

    .navitem.active {
      background: var(--accent-dim);
    }

    .content {
      flex: 1;
      padding: 24px;
    }
  }
</style>
