<script lang="ts">
  import { setDroneEnabled } from '../lib/drone'
  import type { MapView as MapViewType } from '../lib/map'
  import { layers } from '../lib/stores/layers.svelte'
  import './layers.css'

  // The Data-layers rail-panel content (time-dock Phase 4): the frequent overlay
  // toggles — drone flights, phone track. Map *style* (base/labels/terrain) is a
  // separate rail panel (MapStyle.svelte); legends live on-map as chips
  // (Map.svelte), shown only while the layer is on. The rail owns open/close;
  // this is body-only. `view` is a prop so this never imports map.ts.
  let { view }: { view?: typeof MapViewType } = $props()

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
    <label class="label-check">
      <input type="checkbox" checked={layers.drone} onchange={onDrone} /> 🚁 Drone flights
    </label>
    {#if layers.droneStatus}<p class="label-hint">{layers.droneStatus}</p>{/if}
  </div>
  <div class="layers-section">
    <label class="label-check">
      <input type="checkbox" checked={layers.phone} onchange={onPhone} /> 📱 Phone track
    </label>
    <div class="label-hint">Google Timeline · follows the time window · colored by mode</div>
    {#if layers.phoneStatus}<p class="label-hint">{layers.phoneStatus}</p>{/if}
  </div>
</div>
