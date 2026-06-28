/**
 * The Layers store — the map-local data-on-map axis (redesign Axis 2; built native
 * in Svelte during the port). Single source of truth for the Layers panel: the base
 * map, terrain, and label settings. Map-local (the engine receives pushes via the
 * panel's change handlers), in contrast to the global Selection axis.
 *
 * The engine (map.ts) also tracks base/terrain/exaggeration, but defaults match this
 * store, so on a fresh load they agree and the panel never needs to read back from
 * the engine — it persists its own state across route remounts as a module singleton.
 */

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
}

export const layers = new LayersStore()
