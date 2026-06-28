<script lang="ts">
  // Hub to the legacy Systems pages until they're ported into the SPA (Phase 2).
  const links = [
    { label: 'Sensors', href: '/sensors', desc: 'Cabin environment + trend charts' },
    { label: 'gpsd', href: '/gpsd', desc: 'GPS receiver status' },
    { label: 'NTP', href: '/ntp', desc: 'Time sync / chrony status' },
  ]
</script>

<header class="page-head">
  <h1>Systems</h1>
  <p class="muted">House power, van, environment, infra. Legacy pages below until ported.</p>
</header>

<div class="link-list">
  {#each links as l (l.href)}
    <a class="link-row" href={l.href}>
      <span class="link-label">{l.label}</span>
      <span class="link-desc muted">{l.desc}</span>
    </a>
  {/each}
</div>
