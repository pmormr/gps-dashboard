/**
 * The Layers store — single source of truth for the rail panels: the base
 * map, theme, terrain, label settings and the data-layer toggles. Map-engine
 * state (the engine receives pushes via the panels' change handlers), in
 * contrast to the global Selection axis; a module singleton, so it persists
 * across route remounts.
 *
 * The engine (map.ts) also tracks base/theme/terrain/exaggeration. Both seed
 * from the persisted style prefs (`mapPrefs.ts`), so on a fresh load the two
 * agree and the panel never needs to read back from the engine. The style
 * slice writes back to localStorage on every change (the root effect below);
 * the data-layer toggles are deliberately session-scoped.
 */

import type { Place } from '../api'
import { ALL_GROUP_KEYS } from '../places'
import type { BasemapTheme, LabelSettings } from '../labels'
import {
  type BaseLayer,
  loadStylePrefs,
  mapGround,
  saveStylePrefs,
  type StylePrefs,
} from '../mapPrefs'

export type { BaseLayer }

const boot = loadStylePrefs()

class LayersStore {
  base = $state<BaseLayer>(boot.base)
  theme = $state<BasemapTheme>(boot.theme)
  refresh = $state(false)
  terrain = $state(boot.terrain)
  exaggeration = $state(boot.exaggeration)

  // Label settings (vector-only): text density + minor-road labels. The POI
  // marks themselves are governed by the shared category selection below.
  labelOffset = $state(boot.labelOffset)
  minorRoads = $state(boot.minorRoads)

  // Drone overlay (a standalone 3D layer, independent of the Selection window).
  drone = $state(false)
  droneStatus = $state('')

  // Phone-history overlay (Google Timeline import). Unlike drone, it *follows* the
  // Selection window — Map.svelte refetches it when the window or this toggle changes.
  phone = $state(false)
  phoneStatus = $state('')

  // Phone live-tier overlay (OwnTracks sync). Window-following like phone, plus
  // a 60 s refresh while on so the latest-fix marker tracks the 5-min sync.
  phoneLive = $state(false)
  phoneLiveStatus = $state('')

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

  /** The persisted slice (mapPrefs.ts) — reading it tracks every field. */
  get stylePrefs(): StylePrefs {
    return {
      base: this.base,
      theme: this.theme,
      terrain: this.terrain,
      exaggeration: this.exaggeration,
      minorRoads: this.minorRoads,
      labelOffset: this.labelOffset,
    }
  }

  /** A snapshot of the label settings for applyLabels/reapply. */
  get labelSettings(): LabelSettings {
    return { offset: this.labelOffset, minorRoads: this.minorRoads, theme: this.theme }
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

// Write-back: one effect over the whole style slice, so no panel handler can
// forget to persist. Module-scoped root — the store outlives every component.
$effect.root(() => {
  $effect(() => {
    saveStylePrefs(layers.stylePrefs)
  })
  // The ground signal for overlay CSS (drive.css compass halo): one attribute
  // on <html>, flipped here rather than per element.
  $effect(() => {
    document.documentElement.dataset.mapGround = mapGround(layers.base, layers.theme)
  })
})
