<script lang="ts">
  import { onMount } from 'svelte'

  import { hookLabels } from '../lib/labels'
  import type { MapView as MapViewType } from '../lib/map'
  import { RAIL_PANELS, type MapChromeDef, type RailPanel } from '../lib/routes'
  import { layers } from '../lib/stores/layers.svelte'
  import './map.css'
  import DataLayers from './DataLayers.svelte'
  import DriveHud from './DriveHud.svelte'
  import InspectPanel from './InspectPanel.svelte'
  import MapStyle from './MapStyle.svelte'
  import TimeDock from './TimeDock.svelte'

  // The shell-owned map chrome: the right icon rail (exclusive-open panels over
  // the singleton `layers` store) and, where declared, the TimeDock overlay.
  // Rendered by App beside whichever map view is routed (`MAP_CHROME`), keyed
  // by route so panels default closed on every map route change. The engine
  // module is already loaded (or loading) by the view; this import resolves
  // to the same cached chunk. The label hook lives here, not in Map, so a
  // fresh load landing on Drive or Weather applies theme-dependent label
  // settings too.
  let { chrome }: { chrome: MapChromeDef } = $props()

  let view = $state<typeof MapViewType | undefined>()
  let openPanel = $state<RailPanel | null>(null)
  const railLabel = $derived(openPanel ? RAIL_PANELS[openPanel].label : '')

  function toggleRail(id: RailPanel): void {
    openPanel = openPanel === id ? null : id
  }

  onMount(() => {
    let cancelled = false
    import('../lib/map').then((mod) => {
      if (cancelled) return
      view = mod.MapView
      hookLabels(mod.MapView, () => layers.labelSettings)
    })
    return () => {
      cancelled = true
    }
  })
</script>

<div class="map-region">
  <div class="map-rail">
    {#each chrome.rail as id (id)}
      <button
        type="button"
        class:active={openPanel === id}
        title={RAIL_PANELS[id].label}
        aria-label={RAIL_PANELS[id].label}
        onclick={() => toggleRail(id)}
      >{RAIL_PANELS[id].icon}</button>
    {/each}
  </div>

  {#if openPanel}
    <div class="rail-card">
      <div class="rail-card-hdr">
        <span>{railLabel}</span>
        <button type="button" aria-label="Close panel" onclick={() => (openPanel = null)}>✕</button>
      </div>
      <div class="rail-card-body">
        {#if openPanel === 'data'}
          <DataLayers {view} />
        {:else if openPanel === 'style'}
          <MapStyle {view} />
        {:else if openPanel === 'drive'}
          <DriveHud />
        {:else}
          <InspectPanel />
        {/if}
      </div>
    </div>
  {/if}

  {#if chrome.timeDock}
    <div class="tl-overlay">
      <TimeDock />
    </div>
  {/if}
</div>
