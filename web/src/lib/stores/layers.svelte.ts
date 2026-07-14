/**
 * The Layers store — single source of truth for the Layers panel: the base
 * map, terrain, and label settings. Map-local (the engine receives pushes via the
 * panel's change handlers), in contrast to the global Selection axis.
 *
 * The engine (map.ts) also tracks base/terrain/exaggeration, but defaults match this
 * store, so on a fresh load they agree and the panel never needs to read back from
 * the engine — it persists its own state across route remounts as a module singleton.
 */

import type { Place } from '../api'
import { ALL_GROUP_KEYS } from '../places'
import type { LabelSettings } from '../labels'

export type BaseLayer = 'osm' | 'usgs'

class LayersStore {
  base = $state<BaseLayer>('osm')
  refresh = $state(false)
  terrain = $state(false)
  exaggeration = $state(1.3)

  // Label settings (vector-only): text density + minor-road labels. The POI
  // marks themselves are governed by the shared category selection below.
  labelOffset = $state(-1)
  minorRoads = $state(true)

  // Drone overlay (a standalone 3D layer, independent of the Selection window).
  drone = $state(false)
  droneStatus = $state('')

  // Phone-history overlay (Google Timeline import). Unlike drone, it *follows* the
  // Selection window — Map.svelte refetches it when the window or this toggle changes.
  phone = $state(false)
  phoneStatus = $state('')

  // Places overlay (the POI tier). Viewport-driven, not time-windowed —
  // Map.svelte refetches it on map movement while it's on. `placeGroups` is
  // THE POI category selection (one control by design): the same
  // category-group chips filter the overlay pins *and* the basemap's own
  // pois marks. Default all-on — the rank×zoom pin gate (places.ts) and the
  // tiles' per-feature min_zoom already curb map noise.
  places = $state(false)
  placesStatus = $state('')
  placeGroups = $state<Set<string>>(new Set(ALL_GROUP_KEYS))
  // A zoom queued by another view ("Show on map" in Places) for Map.svelte
  // to consume once the engine is mounted — the engine may not exist yet when
  // the navigation happens.
  pendingZoom = $state<{ lat: number; lon: number; zoom: number } | null>(null)

  // Search-results overlay: a value snapshot of the Places view's result set
  // ("Show results on map") — not live; the next push replaces it, the legend
  // chip's ✕ clears it. Store-held so it survives route remounts (toggle
  // Places ↔ Map freely). pendingFit is the one-shot fit-bounds request,
  // consumed like pendingZoom.
  searchResults = $state<Place[] | null>(null)
  searchResultsLabel = $state('')
  pendingFit = $state<{ lat: number; lon: number }[] | null>(null)

  /** Whether the vector basemap (which alone has labels) is active. */
  get isVector(): boolean {
    return this.base === 'osm'
  }

  /** A snapshot of the label settings for applyLabels/reapply. */
  get labelSettings(): LabelSettings {
    return { offset: this.labelOffset, minorRoads: this.minorRoads }
  }

  /** Toggle a POI category group (reassigns the Set so $state reacts). */
  togglePlaceGroup(key: string, on: boolean): void {
    const next = new Set(this.placeGroups)
    if (on) next.add(key)
    else next.delete(key)
    this.placeGroups = next
  }
}

export const layers = new LayersStore()
