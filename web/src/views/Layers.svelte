<script lang="ts">
  import { setDroneEnabled } from '../lib/drone'
  import { POI_GROUPS, reapply } from '../lib/labels'
  import type { MapView as MapViewType } from '../lib/map'
  import { layers, type BaseLayer } from '../lib/stores/layers.svelte'
  import './layers.css'

  // The Layers rail-panel content (time-dock Phase 4): **Data layers** first
  // (frequent toggles — drone, phone), **Map style** (base map + labels + terrain,
  // rare) second and collapsed by default. Legends live on-map as chips
  // (Map.svelte), shown only while the layer is on. Binds to the map-local
  // `layers` store and pushes intent into the engine via the passed MapView
  // façade (`view` is a prop so this never imports map.ts — that would pull
  // MapLibre into the main bundle). The rail owns open/close; this is body-only.
  let { view }: { view?: typeof MapViewType } = $props()

  let styleOpen = $state(false)

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

  async function onDrone(e: Event): Promise<void> {
    const on = (e.currentTarget as HTMLInputElement).checked
    layers.drone = on
    if (!view) return
    if (on) layers.droneStatus = 'Loading…'
    try {
      layers.droneStatus = await setDroneEnabled(view, on)
    } catch (err) {
      layers.droneStatus = `Error: ${err instanceof Error ? err.message : String(err)}`
    }
  }

  // Phone history follows the Selection window, so the toggle only flips the store;
  // Map.svelte's effect does the windowed fetch/clear and writes phoneStatus.
  function onPhone(e: Event): void {
    const on = (e.currentTarget as HTMLInputElement).checked
    layers.phone = on
    layers.phoneStatus = on ? 'Loading…' : ''
  }
</script>

<div class="layers-panel">
  <div class="layers-section">
    <h4>Data layers</h4>
    <label class="label-check">
      <input type="checkbox" checked={layers.drone} onchange={onDrone} /> 🚁 Drone flights
    </label>
    {#if layers.droneStatus}<p class="label-hint">{layers.droneStatus}</p>{/if}
    <label class="label-check">
      <input type="checkbox" checked={layers.phone} onchange={onPhone} /> 📱 Phone track
    </label>
    <div class="label-hint">Google Timeline · follows the time window · colored by mode</div>
    {#if layers.phoneStatus}<p class="label-hint">{layers.phoneStatus}</p>{/if}
  </div>

  <div class="layers-section">
    <button class="layers-style-toggle" type="button" onclick={() => (styleOpen = !styleOpen)}>
      <h4>Map style</h4>
      <span class="layers-style-caret">{styleOpen ? '▾' : '▸'}</span>
    </button>

    {#if styleOpen}
      <div class="layers-style-body">
        <div class="layers-subsection">
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
          <div class="layers-subsection">
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

        <div class="layers-subsection">
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
    {/if}
  </div>
</div>
