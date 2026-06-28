<script lang="ts">
  // Hub to the legacy GNSS-observatory pages until they're ported (Phase 2/3).
  const links = [
    { label: 'Globe', href: '/globe', desc: '3D constellation (PC only)' },
    { label: 'Passes', href: '/passes', desc: 'Upcoming satellite passes' },
    { label: 'Skyplot', href: '/skyplot', desc: 'Live satellite hemisphere' },
  ]
</script>

<header class="page-head">
  <h1>Sky</h1>
  <p class="muted">GNSS observatory. Legacy pages below until ported.</p>
</header>

<div class="link-list">
  {#each links as l (l.href)}
    <a class="link-row" href={l.href}>
      <span class="link-label">{l.label}</span>
      <span class="link-desc muted">{l.desc}</span>
    </a>
  {/each}
</div>
