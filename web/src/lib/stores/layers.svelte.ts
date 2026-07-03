/**
 * The Layers store — single source of truth for the Layers panel: the base
 * map, terrain, and label settings. Map-local (the engine receives pushes via the
 * panel's change handlers), in contrast to the global Selection axis.
 *
 * The engine (map.ts) also tracks base/terrain/exaggeration, but defaults match this
 * store, so on a fresh load they agree and the panel never needs to read back from
 * the engine — it persists its own state across route remounts as a module singleton.
 */

import type { AttractionKind } from '../api'
import { KIND_META } from '../attractions'
import { POI_GROUPS, type LabelSettings } from '../labels'

export type BaseLayer = 'osm' | 'usgs'

class LayersStore {
  base = $state<BaseLayer>('osm')
  refresh = $state(false)
  terrain = $state(false)
  exaggeration = $state(1.3)

  // Label settings (vector-only). Groups default all-on.
  labelGroups = $state<Set<string>>(new Set(Object.keys(POI_GROUPS)))
  labelOffset = $state(-1)
  minorRoads = $state(true)

  // Drone overlay (a standalone 3D layer, independent of the Selection window).
  drone = $state(false)
  droneStatus = $state('')

  // Phone-history overlay (Google Timeline import). Unlike drone, it *follows* the
  // Selection window — Map.svelte refetches it when the window or this toggle changes.
  phone = $state(false)
  phoneStatus = $state('')

  // Attractions overlay (parks/public-lands POI tier). Viewport-driven, not
  // time-windowed — Map.svelte refetches it on map movement while it's on.
  // Kinds default all-on; the zoom gate (attractions.ts) still applies on top.
  attractions = $state(false)
  attractionsStatus = $state('')
  attractionKinds = $state<Set<AttractionKind>>(new Set(KIND_META.map((m) => m.kind)))

  /** Whether the vector basemap (which alone has labels) is active. */
  get isVector(): boolean {
    return this.base === 'osm'
  }

  /** A snapshot of the label settings for applyLabels/reapply. */
  get labelSettings(): LabelSettings {
    return { groups: this.labelGroups, offset: this.labelOffset, minorRoads: this.minorRoads }
  }

  /** Toggle a POI category (reassigns the Set so $state reacts). */
  toggleGroup(group: string, on: boolean): void {
    const next = new Set(this.labelGroups)
    if (on) next.add(group)
    else next.delete(group)
    this.labelGroups = next
  }

  /** Toggle an attraction kind (reassigns the Set so $state reacts). */
  toggleAttractionKind(kind: AttractionKind, on: boolean): void {
    const next = new Set(this.attractionKinds)
    if (on) next.add(kind)
    else next.delete(kind)
    this.attractionKinds = next
  }
}

export const layers = new LayersStore()
