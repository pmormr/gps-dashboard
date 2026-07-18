<script lang="ts">
  import { BASEMAP_THEMES, reapply, type BasemapTheme } from '../lib/labels'
  import type { MapView as MapViewType } from '../lib/map'
  import { layers, type BaseLayer } from '../lib/stores/layers.svelte'
  import './layers.css'

  // The Map-style rail-panel content: base map + minor-road labels +
  // 3D terrain — the rarely-touched styling, split from the Data-layers panel
  // (DataLayers.svelte). Everything POI — category chips *and* the density
  // slider — lives in Data layers, one panel for "which POIs, how many".
  // Binds to the map-local `layers` store and pushes intent
  // into the engine via the passed MapView façade (`view` is a prop so this never
  // imports map.ts — that would pull MapLibre into the main bundle). The rail
  // owns open/close; this is body-only.
  let { view }: { view?: typeof MapViewType } = $props()

  function onBase(e: Event): void {
    const base = (e.currentTarget as HTMLSelectElement).value as BaseLayer
    layers.base = base
    view?.setLayer(base)
    // Refresh is meaningless for the immutable vector base — clear it on switch.
    if (layers.isVector && layers.refresh) {
      layers.refresh = false
      view?.setRefreshMode(false)
    }
  }

  function onTheme(e: Event): void {
    layers.theme = (e.currentTarget as HTMLSelectElement).value as BasemapTheme
    view?.setVectorTheme(layers.theme)
  }

  function onRefresh(e: Event): void {
    layers.refresh = (e.currentTarget as HTMLInputElement).checked
    view?.setRefreshMode(layers.refresh)
  }

  function onMinor(e: Event): void {
    layers.minorRoads = (e.currentTarget as HTMLInputElement).checked
    if (view) reapply(view, layers.labelSettings)
  }

  function onTerrain(e: Event): void {
    layers.terrain = (e.currentTarget as HTMLInputElement).checked
    view?.setTerrainEnabled(layers.terrain)
  }

  function onExag(): void {
    view?.setExaggeration(layers.exaggeration)
  }
</script>

<div class="layers-panel">
  <div class="layers-section">
    <h4>Base map</h4>
    <select value={layers.base} onchange={onBase}>
      <option value="osm">OSM (vector)</option>
      <option value="usgs">USGS Topo</option>
    </select>
    {#if layers.isVector}
      <div class="label-row">
        <h4>Theme</h4>
        <select value={layers.theme} onchange={onTheme}>
          {#each BASEMAP_THEMES as t (t.id)}
            <option value={t.id}>{t.label}</option>
          {/each}
        </select>
      </div>
      <label class="label-check">
        <input type="checkbox" checked={layers.minorRoads} onchange={onMinor} /> Minor street names (z13+)
      </label>
    {/if}
    {#if !layers.isVector}
      <label class="label-check">
        <input type="checkbox" checked={layers.refresh} onchange={onRefresh} /> ↻ Refresh tiles
      </label>
      <div class="label-hint">re-checks upstream; reload to see updates</div>
    {/if}
  </div>

  <div class="layers-section">
    <h4>3D terrain</h4>
    <label class="label-check">
      <input type="checkbox" checked={layers.terrain} onchange={onTerrain} /> Drape on terrain
    </label>
    {#if layers.terrain}
      <div class="label-row">
        <h4>Exaggeration <span class="label-val">{layers.exaggeration.toFixed(1)}×</span></h4>
        <input type="range" min="0.5" max="8" step="0.1" bind:value={layers.exaggeration} oninput={onExag} />
        <div class="label-hint">drag to drive a mountain harder</div>
      </div>
    {/if}
  </div>
</div>
