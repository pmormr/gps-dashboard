<script lang="ts">
  import { onMount } from 'svelte'

  import { router } from '../lib/router.svelte'
  import './globe.css'

  let root: HTMLDivElement

  // Lazy create/destroy: the WebGL context lives only while this view is mounted
  // (contexts are scarce, and we never keep more than one alive). The renderer
  // (and three.js, ~160 kB gz) is dynamic-imported so it stays out of the main
  // bundle — only this PC-only view pays for it, on first visit.
  onMount(() => {
    let cleanup: (() => void) | undefined
    let cancelled = false
    import('../lib/globe').then(({ mountGlobe }) => {
      if (!cancelled) cleanup = mountGlobe(root)
    })
    return () => {
      cancelled = true
      cleanup?.()
    }
  })
</script>

<div class="globe-island" bind:this={root}>
  <div id="globe-container"></div>
  <div id="status">Loading constellation…</div>

  <div class="panel">
    <div class="nav">
      <button onclick={() => router.navigate('/sky')}>← Sky</button>
      <button onclick={() => router.navigate('/skyplot')}>Skyplot</button>
    </div>
    <h1>Constellation Globe</h1>
    <div class="sub">
      Satellites we've logged, reconstructed in 3D · drag to orbit · scroll to zoom
    </div>

    <div class="row" id="windows">
      <button class="chip" data-h="1">1h</button>
      <button class="chip active" data-h="24">24h</button>
      <button class="chip" data-h="168">7d</button>
    </div>
    <div class="row">
      <button class="chip active" id="t-orbits">Orbits</button>
      <button class="chip" id="t-trails">Trails</button>
      <button class="chip active" id="t-rays">Sight lines</button>
      <button class="chip active" id="t-spin">Auto-spin</button>
    </div>

    <div class="legend" id="legend"></div>
    <div class="info" id="info">—</div>
  </div>

  <div class="hint">PC view · drag to orbit · scroll to zoom · click a satellite</div>
  <div id="sat-popup" class="popup hidden"></div>
</div>
