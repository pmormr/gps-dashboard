<script lang="ts">
  import { CATEGORY_GROUPS } from '../lib/places'
  import { errMsg } from '../lib/errors'
  import { setDroneEnabled } from '../lib/drone'
  import { reapply } from '../lib/labels'
  import type { MapView as MapViewType } from '../lib/map'
  import { layers } from '../lib/stores/layers.svelte'
  import './layers.css'
  import PoiIcon from './PoiIcon.svelte'

  // The Data-layers rail-panel content: the frequent overlay
  // toggles — drone flights, phone track, places — plus everything POI: the
  // category chips and the density slider, one panel for "which POIs, how
  // many". Map *style* (base/roads/terrain) is a separate rail panel
  // (MapStyle.svelte); legends live on-map as chips (Map.svelte), shown only
  // while the layer is on. The rail owns open/close; this is body-only.
  // `view` is a prop so this never imports map.ts.
  let { view }: { view?: typeof MapViewType } = $props()

  async function onDrone(e: Event): Promise<void> {
    const on = (e.currentTarget as HTMLInputElement).checked
    layers.drone = on
    if (!view) return
    if (on) layers.droneStatus = 'Loading…'
    try {
      layers.droneStatus = await setDroneEnabled(view, on)
    } catch (err) {
      layers.droneStatus = `Error: ${errMsg(err)}`
    }
  }

  // Phone history follows the Selection window, so the toggle only flips the store;
  // Map.svelte's effect does the windowed fetch/clear and writes phoneStatus.
  function onPhone(e: Event): void {
    const on = (e.currentTarget as HTMLInputElement).checked
    layers.phone = on
    layers.phoneStatus = on ? 'Loading…' : ''
  }

  // Phone live (OwnTracks) follows the same store-flip pattern; Map.svelte's
  // effect owns the fetch plus a 60 s refresh while on.
  function onPhoneLive(e: Event): void {
    const on = (e.currentTarget as HTMLInputElement).checked
    layers.phoneLive = on
    layers.phoneLiveStatus = on ? 'Loading…' : ''
  }

  // Places are viewport-driven; like phone, the toggle only flips the store —
  // Map.svelte's effect does the bbox fetch/clear and writes placesStatus.
  function onPlaces(e: Event): void {
    const on = (e.currentTarget as HTMLInputElement).checked
    layers.places = on
    layers.placesStatus = on ? 'Loading…' : ''
  }

  function onPlaceGroup(key: string, e: Event): void {
    layers.togglePlaceGroup(key, (e.currentTarget as HTMLInputElement).checked)
  }

  // The density slider gates the basemap's own marks (labels.ts zoom offset);
  // the places-overlay pins follow via Map.svelte's effect reading labelOffset.
  function onOffset(): void {
    if (view) reapply(view, layers.labelSettings)
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
  <div class="layers-section">
    <label class="label-check">
      <input type="checkbox" checked={layers.phoneLive} onchange={onPhoneLive} /> 📍 Phone live
    </label>
    <div class="label-hint">OwnTracks · follows the time window · ringed dot = latest fix</div>
    {#if layers.phoneLiveStatus}<p class="label-hint">{layers.phoneLiveStatus}</p>{/if}
  </div>
  <div class="layers-section">
    <label class="label-check">
      <input type="checkbox" checked={layers.places} onchange={onPlaces} /> 🏞 Places
    </label>
    <div class="label-hint">POIs (NPS · federal · OSM) · follows the map view · more pins as you zoom</div>
    {#if layers.placesStatus}<p class="label-hint">{layers.placesStatus}</p>{/if}
  </div>
  <div class="layers-section">
    <h4>POI categories</h4>
    <div class="label-hint">one filter: the basemap's own marks + the Places pins</div>
    <div class="place-kinds">
      {#each CATEGORY_GROUPS as g (g.key)}
        <label class="label-check">
          <input
            type="checkbox"
            checked={layers.placeGroups.has(g.key)}
            onchange={(e) => onPlaceGroup(g.key, e)}
          />
          <PoiIcon icon={g.sprite} color={g.color} size={14} />
          {g.label}
        </label>
      {/each}
    </div>
    <div class="label-row">
      <h4>POI density <span class="label-val">{layers.labelOffset}</span></h4>
      <input type="range" min="-4" max="0" step="1" bind:value={layers.labelOffset} oninput={onOffset} />
      <div class="label-hint">left = POI marks &amp; pins appear earlier / denser</div>
    </div>
  </div>
</div>
