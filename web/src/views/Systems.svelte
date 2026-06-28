<script lang="ts">
  import { router } from '../lib/router.svelte'

  // `to` = a ported SPA view (client nav); `href` = a not-yet-ported legacy page.
  interface SysLink {
    label: string
    desc: string
    to?: string
    href?: string
  }

  const links: SysLink[] = [
    { label: 'NTP', desc: 'Time sync / chrony status', to: '/ntp' },
    { label: 'Sensors', desc: 'Cabin environment + trend charts', href: '/sensors' },
    { label: 'gpsd', desc: 'GPS receiver status', href: '/gpsd' },
  ]
</script>

<header class="page-head">
  <h1>Systems</h1>
  <p class="muted">House power, van, environment, infra. Legacy pages until ported.</p>
</header>

<div class="link-list">
  {#each links as l (l.label)}
    {#if l.to}
      <button class="link-row" onclick={() => router.navigate(l.to!)}>
        <span class="link-label">{l.label}</span>
        <span class="link-desc muted">{l.desc}</span>
      </button>
    {:else}
      <a class="link-row" href={l.href}>
        <span class="link-label">{l.label}</span>
        <span class="link-desc muted">{l.desc}</span>
      </a>
    {/if}
  {/each}
</div>

<style>
  /* The client-nav row is a <button>; match the <a> rows' look. */
  button.link-row {
    width: 100%;
    text-align: left;
    font: inherit;
    cursor: pointer;
  }
</style>
