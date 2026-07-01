<script lang="ts">
  import { onMount } from 'svelte'

  import { getPointsLatest } from '../lib/api'
  import { hookLabels } from '../lib/labels'
  import type { MapView as MapViewType } from '../lib/map'
  import { clearPhone, syncPhone } from '../lib/phone'
  import { annotations } from '../lib/stores/annotations.svelte'
  import { layers } from '../lib/stores/layers.svelte'
  import { selection } from '../lib/stores/selection.svelte'
  import './map.css'
  import './annotations.css'
  import AnnotationForm from './AnnotationForm.svelte'
  import AnnotationsDrawer from './AnnotationsDrawer.svelte'
  import InspectPanel from './InspectPanel.svelte'
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

  // Phone-history overlay follows the global time window: refetch when the window
  // or the toggle changes (and once `view` lands). `range` is only read while the
  // layer is on, so an off overlay doesn't refetch on every scrub.
  $effect(() => {
    if (!view) return
    if (!layers.phone) {
      clearPhone(view)
      return
    }
    const range = selection.range
    syncPhone(view, range.from.toISOString(), range.to.toISOString())
      .then((label) => {
        if (label) layers.phoneStatus = label
      })
      .catch((err) => {
        layers.phoneStatus = `Error: ${err instanceof Error ? err.message : String(err)}`
      })
  })

  // Recenter the map on the latest *raw* fix (the true current position).
  async function zoomToCurrent(): Promise<void> {
    try {
      const pt = await getPointsLatest()
      if (pt && pt.lat != null && pt.lon != null) view?.zoomTo(pt.lat, pt.lon, 17)
    } catch {
      /* no fix available — ignore */
    }
  }
</script>

<div class="map-region">
  <div class="map-chrome-tl">
    <button class="map-ann-toggle" class:active={drawerOpen} onclick={() => (drawerOpen = !drawerOpen)}>
      <span>📍 Annotations</span>
      {#if annotations.count}<span class="ann-count">{annotations.count}</span>{/if}
    </button>
    <button class="map-fab" title="Zoom to current location" onclick={zoomToCurrent}>⊕</button>
  </div>

  <div class="map-chrome-tr">
    <Layers {view} />
    <MarksPanel />
    <InspectPanel />
  </div>

  <div class="tl-overlay">
    <Timeline {view} />
  </div>
</div>

<AnnotationsDrawer bind:open={drawerOpen} />
<AnnotationForm />
