<script lang="ts">
  import { onMount } from 'svelte'

  import type { MapView as MapViewType } from '../lib/map'
  import './map.css'
  import Timeline from './Timeline.svelte'

  // Map view: the persistent engine (mapHost.ts) + Svelte chrome. The engine
  // (map.ts + MapLibre, ~283 kB gz) is dynamic-imported so it's a Map-only chunk;
  // the main bundle stays small. `view` is passed to chrome children (Timeline) so
  // they never import map.ts directly. Layer/3D chrome resyncs from the engine on
  // mount (the engine persists across routes; this view remounts).
  let view = $state<typeof MapViewType | undefined>()
  let layer = $state('osm')
  let terrain = $state(false)
  let exaggeration = $state(1.3)

  onMount(() => {
    let cancelled = false
    let hide: (() => void) | undefined
    Promise.all([import('../lib/mapHost'), import('../lib/map')]).then(([host, mod]) => {
      if (cancelled) return
      view = mod.MapView
      host.showMap()
      hide = host.hideMap
      // Resync chrome to the (persisted) engine state, not this view's defaults.
      layer = mod.MapView.getLayer()
      terrain = mod.MapView.getTerrainEnabled()
      exaggeration = mod.MapView.getExaggeration()
    })
    return () => {
      cancelled = true
      hide?.()
    }
  })

  function onLayer(): void {
    view?.setLayer(layer)
  }
  function onTerrain(): void {
    view?.setTerrainEnabled(terrain)
  }
  function onExag(): void {
    view?.setExaggeration(exaggeration)
  }
</script>

<div class="map-region">
  <div class="map-chrome-tr">
    <select bind:value={layer} onchange={onLayer} title="Map layer">
      <option value="osm">OSM</option>
      <option value="usgs">USGS Topo</option>
    </select>
    <label class="map-ctl">
      <input type="checkbox" bind:checked={terrain} onchange={onTerrain} /> 3D terrain
    </label>
    {#if terrain}
      <label class="map-ctl">
        ⛰ {exaggeration.toFixed(1)}×
        <input type="range" min="0.5" max="8" step="0.1" bind:value={exaggeration} oninput={onExag} />
      </label>
    {/if}
  </div>

  <div class="tl-overlay">
    <Timeline {view} />
  </div>
</div>
