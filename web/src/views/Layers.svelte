<script lang="ts">
  import { POI_GROUPS, reapply } from '../lib/labels'
  import type { MapView as MapViewType } from '../lib/map'
  import { layers, type BaseLayer } from '../lib/stores/layers.svelte'
  import './layers.css'

  // The unified Layers panel (redesign Axis 2): base map + labels + 3D terrain in
  // one collapsible panel (folding in the legacy ⚙ Labels / 🏔 3D / 🚁 Drone floats;
  // drone lands in sub-step 5). Binds to the map-local `layers` store and pushes
  // intent into the engine via the passed MapView façade (`view` is a prop so this
  // never imports map.ts — that would pull MapLibre into the main bundle).
  let { view }: { view?: typeof MapViewType } = $props()

  let collapsed = $state(false)

  const GROUPS = Object.keys(POI_GROUPS)

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

  function onRefresh(e: Event): void {
    layers.refresh = (e.currentTarget as HTMLInputElement).checked
    view?.setRefreshMode(layers.refresh)
  }

  function onGroup(group: string, e: Event): void {
    layers.toggleGroup(group, (e.currentTarget as HTMLInputElement).checked)
    if (view) reapply(view, layers.labelSettings)
  }

  function onOffset(): void {
    if (view) reapply(view, layers.labelSettings)
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

<div class="floating-panel layers-panel" class:collapsed>
  <button class="floating-panel-hdr" type="button" onclick={() => (collapsed = !collapsed)}>
    🗺 Layers
  </button>
  <div class="floating-panel-body">
    <div class="layers-section">
      <h4>Base map</h4>
      <select value={layers.base} onchange={onBase}>
        <option value="osm">OSM (vector)</option>
        <option value="usgs">USGS Topo</option>
      </select>
      {#if !layers.isVector}
        <label class="label-check">
          <input type="checkbox" checked={layers.refresh} onchange={onRefresh} /> ↻ Refresh tiles
        </label>
        <div class="label-hint">re-checks upstream; reload to see updates</div>
      {/if}
    </div>

    {#if layers.isVector}
      <div class="layers-section">
        <h4>Labels</h4>
        <div class="label-cats">
          {#each GROUPS as g (g)}
            <label class="label-check">
              <input type="checkbox" checked={layers.labelGroups.has(g)} onchange={(e) => onGroup(g, e)} />
              {g}
            </label>
          {/each}
        </div>
        <div class="label-row">
          <h4>Density <span class="label-val">{layers.labelOffset}</span></h4>
          <input type="range" min="-4" max="0" step="1" bind:value={layers.labelOffset} oninput={onOffset} />
          <div class="label-hint">left = labels appear earlier / denser</div>
        </div>
        <label class="label-check">
          <input type="checkbox" checked={layers.minorRoads} onchange={onMinor} /> Minor street names (z13+)
        </label>
      </div>
    {/if}

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
</div>
