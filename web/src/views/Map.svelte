<script lang="ts">
  import { onMount } from 'svelte'

  import { hookLabels } from '../lib/labels'
  import type { MapView as MapViewType } from '../lib/map'
  import { annotations } from '../lib/stores/annotations.svelte'
  import { layers } from '../lib/stores/layers.svelte'
  import './map.css'
  import './annotations.css'
  import AnnotationForm from './AnnotationForm.svelte'
  import AnnotationsDrawer from './AnnotationsDrawer.svelte'
  import Layers from './Layers.svelte'
  import MarksPanel from './MarksPanel.svelte'
  import Timeline from './Timeline.svelte'

  // Map view: the persistent engine (mapHost.ts) + Svelte chrome. The engine
  // (map.ts + MapLibre, ~283 kB gz) is dynamic-imported so it's a Map-only chunk;
  // the main bundle stays small. `view` is passed to chrome children (Timeline,
  // Layers) so they never import map.ts directly. Map-local state (Layers/Marks)
  // lives in stores that persist across the cheap chrome remount; the engine itself
  // persists too, so on a fresh load the two agree without reading back.
  let view = $state<typeof MapViewType | undefined>()
  let drawerOpen = $state(false)

  onMount(() => {
    let cancelled = false
    let hide: (() => void) | undefined
    Promise.all([import('../lib/mapHost'), import('../lib/map')]).then(([host, mod]) => {
      if (cancelled) return
      view = mod.MapView
      host.showMap()
      hide = host.hideMap
      // Re-apply label settings whenever the vector base (re)loads its style.
      hookLabels(mod.MapView, () => layers.labelSettings)
    })
    annotations.reload()
    return () => {
      cancelled = true
      hide?.()
    }
  })
</script>

<div class="map-region">
  <div class="map-chrome-tl">
    <button class="map-ann-toggle" class:active={drawerOpen} onclick={() => (drawerOpen = !drawerOpen)}>
      <span>📍 Annotations</span>
      {#if annotations.count}<span class="ann-count">{annotations.count}</span>{/if}
    </button>
  </div>

  <div class="map-chrome-tr">
    <Layers {view} />
    <MarksPanel />
  </div>

  <div class="tl-overlay">
    <Timeline {view} />
  </div>
</div>

<AnnotationsDrawer bind:open={drawerOpen} />
<AnnotationForm />
