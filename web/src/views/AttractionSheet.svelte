<script lang="ts">
  import type { MapView as MapViewType } from '../lib/map'
  import AttractionDetail from './AttractionDetail.svelte'

  // The map-side attraction container (side panel desktop / bottom sheet mobile),
  // opened by a waypoint click. All content lives in the shared AttractionDetail;
  // here "Show on map" just zooms the already-visible map and closes the sheet.
  let {
    id,
    view,
    onClose,
  }: { id: number; view?: typeof MapViewType; onClose: () => void } = $props()

  function showOnMap(lat: number, lon: number, zoom: number): void {
    view?.zoomTo(lat, lon, zoom)
    onClose()
  }
</script>

<div class="attr-sheet">
  <div class="attr-sheet-hdr">
    <span class="attr-sheet-hdr-label">Attraction</span>
    <button type="button" aria-label="Close" onclick={onClose}>✕</button>
  </div>
  <div class="attr-sheet-body">
    <AttractionDetail {id} onShowMap={showOnMap} />
  </div>
</div>
