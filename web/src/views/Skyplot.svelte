<script lang="ts">
  import { onMount } from 'svelte'

  import { router } from '../lib/router.svelte'
  import { mountSkyplot } from '../lib/skyplot'
  import './skyplot.css'

  let root: HTMLDivElement

  // Lazy create/destroy: polling gpsd only while the view is mounted. onMount's
  // returned cleanup fires on unmount.
  onMount(() => mountSkyplot(root))
</script>

<header class="page-head">
  <h1>Skyplot</h1>
  <p class="muted">
    <button class="back" onclick={() => router.navigate('/sky')}>← Sky</button>
    · Live 3D sky · drag to rotate · reads gpsd directly, no history stored
  </p>
</header>

<div class="skyplot-island" bind:this={root}>
  <div class="card">
    <div id="sky-wrap">
      <canvas id="sky"></canvas>
      <div id="sky-status" class="show">Connecting to gpsd…</div>
    </div>

    <div class="controls">
      <button class="btn" id="btn-topdown">Top-down</button>
      <button class="btn" id="btn-reset">Reset view</button>
      <button class="btn toggle" id="btn-trails">Trails</button>
      <button class="btn toggle" id="btn-vectors">Vectors</button>
      <button class="btn toggle" id="btn-footprint">Footprint</button>
      <button class="btn toggle" id="btn-geometry">Geometry</button>
      <button class="btn toggle" id="btn-passes">Predicted</button>
    </div>

    <div class="legend" id="legend"></div>

    <div class="dop">
      <div class="dop-title">Dilution of precision (geometry)</div>
      <div class="dop-row">
        <span class="dop-k">HDOP</span>
        <div class="dop-track"><div class="dop-mark" id="dop-h-mark"></div></div>
        <span class="dop-v" id="dop-h-v">—</span>
      </div>
      <div class="dop-row">
        <span class="dop-k">VDOP</span>
        <div class="dop-track"><div class="dop-mark" id="dop-v-mark"></div></div>
        <span class="dop-v" id="dop-v-v">—</span>
      </div>
      <div class="dop-row">
        <span class="dop-k">PDOP</span>
        <div class="dop-track"><div class="dop-mark" id="dop-p-mark"></div></div>
        <span class="dop-v" id="dop-p-v">—</span>
      </div>
      <div class="dop-scale"><span>0</span><span>2</span><span>5</span><span>10+</span></div>
    </div>

    <div class="stats">
      <div class="stat"><div class="k">Used / Seen</div><div class="v" id="st-sats">— / —</div></div>
      <div class="stat"><div class="k">Fix</div><div class="v" id="st-fix">—</div></div>
      <div class="stat"><div class="k">Heading</div><div class="v" id="st-head">—</div></div>
      <div class="stat"><div class="k">Speed</div><div class="v" id="st-speed">—</div></div>
      <div class="stat"><div class="k">Quality</div><div class="v" id="st-quality">—</div></div>
      <div class="stat"><div class="k">Updated</div><div class="v" id="st-age">—</div></div>
    </div>
  </div>
</div>

<style>
  .back {
    background: none;
    border: none;
    color: var(--accent);
    font: inherit;
    padding: 0;
    cursor: pointer;
  }
</style>
