<script lang="ts">
  import type { MapView as MapViewType } from '../lib/map'
  import PlaceDetail from './PlaceDetail.svelte'

  // The map-side place container (side panel desktop / bottom sheet mobile),
  // opened by a waypoint click. All content lives in the shared PlaceDetail;
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
    <span class="attr-sheet-hdr-label">Place</span>
    <button type="button" aria-label="Close" onclick={onClose}>✕</button>
  </div>
  <div class="attr-sheet-body">
    <PlaceDetail {id} onShowMap={showOnMap} />
  </div>
</div>
