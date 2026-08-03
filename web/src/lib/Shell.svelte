<script lang="ts">
  import type { Snippet } from 'svelte'

  import { router } from './router.svelte'
  import { NAV, PHONE_PRIMARY_TABS, SECTIONS } from './routes'
  import SectionNav from './SectionNav.svelte'

  let { children }: { children: Snippet } = $props()

  // Phone bottom bar shows the primary tabs + a "More" sheet for the rest; the
  // desktop sidebar shows all of NAV (CSS reveals the overflow items there).
  let moreOpen = $state(false)
  const overflowNav = NAV.filter((n) => !PHONE_PRIMARY_TABS.includes(n.to))
  const inOverflow = $derived(!PHONE_PRIMARY_TABS.includes(router.current.tab))

  function go(to: string): void {
    router.navigate(to)
    moreOpen = false
  }
</script>

<div class="shell">
  <nav class="nav">
    <div class="brand">Van OS</div>
    <ul>
      {#each NAV as item (item.label)}
        {@const subs = SECTIONS[item.to]}
        <li class:nav-overflow={!PHONE_PRIMARY_TABS.includes(item.to)}>
          <button
            class="navitem"
            class:active={router.current.tab === item.to}
            onclick={() => go(item.to)}
          >
            <span class="icon">{item.icon}</span>
            <span class="label">{item.label}</span>
          </button>
          <!-- Desktop: the section's sub-destinations expand under the active tab
               (CSS hides this on phones, where SectionNav's pill strip stands in). -->
          {#if subs && router.current.tab === item.to}
            <ul class="subnav">
              {#each subs as sub (sub.to)}
                <li>
                  <button
                    class="subitem"
                    class:active={router.path === sub.to}
                    onclick={() => go(sub.to)}
                  >
                    {sub.label}
                  </button>
                </li>
              {/each}
            </ul>
          {/if}
        </li>
      {/each}
      <li class="nav-more">
        <button class="navitem" class:active={inOverflow} onclick={() => (moreOpen = !moreOpen)}>
          <span class="icon">⋯</span>
          <span class="label">More</span>
        </button>
      </li>
    </ul>
  </nav>

  {#if moreOpen}
    <button class="more-backdrop" aria-label="Close menu" onclick={() => (moreOpen = false)}></button>
    <div class="more-sheet">
      {#each overflowNav as item (item.to)}
        <button
          class="more-item"
          class:active={router.current.tab === item.to}
          onclick={() => go(item.to)}
        >
          <span class="icon">{item.icon}</span><span>{item.label}</span>
        </button>
      {/each}
    </div>
  {/if}

  <main class="content">
    <SectionNav />
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

  /* Sub-destinations nested under a tab — desktop sidebar only (the bottom bar
     can't nest; SectionNav's top pills cover phones). Revealed at the breakpoint. */
  .subnav {
    display: none;
    list-style: none;
    margin: 0;
    padding: 0;
  }

  /* Phone: the overflow tabs live in the More sheet, not the bar. */
  @media (max-width: 767px) {
    .nav-overflow {
      display: none;
    }
  }
  .more-backdrop {
    position: fixed;
    inset: 0;
    z-index: 90;
    background: rgba(0, 0, 0, 0.45);
    border: none;
    cursor: default;
  }
  .more-sheet {
    position: fixed;
    left: 0;
    right: 0;
    bottom: calc(var(--nav-h) + env(safe-area-inset-bottom));
    z-index: 95;
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 8px;
    padding: 12px;
    background: var(--surface);
    border-top: 1px solid var(--border);
  }
  .more-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 14px;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--text);
    font: inherit;
    font-size: 15px;
    cursor: pointer;
  }
  .more-item.active {
    color: var(--accent);
    border-color: var(--accent);
  }
  .more-item .icon {
    font-size: 18px;
  }

  @media (min-width: 768px) {
    /* Desktop sidebar shows every tab — no overflow, no More. */
    .nav-more,
    .more-sheet,
    .more-backdrop {
      display: none;
    }
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

    .subnav {
      display: block;
      padding: 2px 0 6px;
    }
    .subitem {
      width: 100%;
      display: block;
      text-align: left;
      /* Indent under the parent's icon+gap so it reads as a child. */
      padding: 8px 20px 8px 50px;
      background: none;
      border: none;
      color: var(--text-dim);
      font: inherit;
      font-size: 14px;
      cursor: pointer;
    }
    .subitem:hover {
      color: var(--text);
    }
    .subitem.active {
      color: var(--accent);
    }

    .content {
      flex: 1;
      padding: 24px;
    }
  }
</style>
