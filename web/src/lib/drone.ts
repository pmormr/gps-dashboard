/**
 * Drone overlay controller. Toggling the Layers-panel drone
 * checkbox on lazily fetches *all* flights once (the dataset is tiny and MapLibre
 * only draws what's in view) and hands them to the MapView façade as a standalone 3D
 * layer, independent of the Selection time window. Re-toggling reuses the cache.
 *
 * The three.js renderer (overlay3d.ts) is dynamic-imported here on first enable, so
 * three stays out of the base map chunk and only loads when drone tracks are wanted.
 */

import { getDroneFlights } from './api'
import type { DroneFlight, MapView as MapViewType } from './map'

type View = typeof MapViewType

let cache: DroneFlight[] = []
let loaded = false
let overlayReady: Promise<unknown> | null = null

/** Import + register the three.js overlay once (registers itself via setOverlay3D). */
function ensureOverlay(): Promise<unknown> {
  if (!overlayReady) overlayReady = import('./overlay3d')
  return overlayReady
}

function flightsLabel(): string {
  return `${cache.length} flight${cache.length === 1 ? '' : 's'}`
}

/**
 * Show or hide the drone overlay. Returns a status string for the panel; throws on a
 * fetch failure (the caller surfaces it). Off is a synchronous clear.
 */
export async function setDroneEnabled(view: View, on: boolean): Promise<string> {
  if (!on) {
    view.clearDroneTracks()
    return loaded ? flightsLabel() : ''
  }
  await ensureOverlay()
  if (!loaded) {
    const data = await getDroneFlights()
    cache = data.flights ?? []
    loaded = true
  }
  view.showDroneTracks(cache)
  return flightsLabel()
}
