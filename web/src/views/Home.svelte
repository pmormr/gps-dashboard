<script lang="ts">
  // Placeholder cards — wired to GET /api/status in the next Phase 1 step.
  const domains = [
    { name: 'House power', metric: '—', sub: 'battery SOC' },
    { name: 'Location', metric: '—', sub: 'current fix' },
    { name: 'Cabin', metric: '—', sub: 'air quality' },
    { name: 'GNSS', metric: '—', sub: 'satellites' },
  ]
</script>

<header class="page-head">
  <h1>Van OS</h1>
  <p class="muted">Command panel — live status wiring is the next step.</p>
</header>

<div class="cards">
  {#each domains as d (d.name)}
    <div class="card">
      <div class="card-metric">{d.metric}</div>
      <div class="card-name">{d.name}</div>
      <div class="card-sub muted">{d.sub}</div>
    </div>
  {/each}
</div>
