<script lang="ts">
  import { BASEMAP_KINDS, CATEGORY_META } from '../lib/icons'
  import type { MapView as MapViewType } from '../lib/map'
  import { facetLabel } from '../lib/places'
  import PlaceDetail from './PlaceDetail.svelte'

  // The map-side place container (side panel desktop / bottom sheet mobile),
  // opened by a waypoint click or a basemap-mark tap. A resolved tap carries a
  // tier row id and renders the shared PlaceDetail; an unresolved mark (the
  // tier has no row — archive/tier vintage skew or an excluded kind) carries a
  // `stub` of the tile's own attrs instead — every mark responds to touch.
  // "Show on map" just zooms the already-visible map and closes the sheet.
  let {
    id,
    stub = null,
    view,
    onClose,
    onOpenPlace,
  }: {
    id: number | null
    stub?: { name: string; kind: string | null; lat: number; lon: number } | null
    view?: typeof MapViewType
    onClose: () => void
    onOpenPlace?: (id: number) => void
  } = $props()

  const stubMeta = $derived.by(() => {
    if (!stub?.kind) return null
    const category = BASEMAP_KINDS[stub.kind]?.category
    return {
      label: facetLabel(stub.kind),
      color: CATEGORY_META.find((m) => m.category === category)?.color ?? '#94a3b8',
    }
  })

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
    {#if id != null}
      <PlaceDetail {id} onShowMap={showOnMap} {onOpenPlace} />
    {:else if stub}
      <div class="attr-detail">
        <h3>{stub.name}</h3>
        {#if stubMeta}
          <p><span class="legend-swatch" style:background={stubMeta.color}></span> {stubMeta.label}</p>
        {/if}
        <p class="attr-hint">
          {stub.lat.toFixed(5)}, {stub.lon.toFixed(5)}
        </p>
        <p class="attr-hint">
          From the map data — not in the places index, so no description or hours are available.
        </p>
      </div>
    {/if}
  </div>
</div>
