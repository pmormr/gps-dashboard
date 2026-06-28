/**
 * Persistent MapLibre host — the keep-alive seam for the map view.
 *
 * Re-creating a maplibregl.Map on every visit costs ~1s and drops tile caches, so
 * the map element is created exactly once and **kept permanently in the DOM** in a
 * body-level host; the Map route only toggles its visibility. Detaching the canvas
 * from the DOM (re-parenting) disrupts MapLibre's rendering, so we hide via
 * `display:none` and `resize()` on show instead — the well-trodden keep-alive path.
 * State (the loaded window, layers, track) lives in the Selection store, so the
 * Svelte chrome around the map is free to unmount/remount and rehydrate.
 */

import { MapView } from './map'

let host: HTMLElement | null = null

/** Create the body-level host + engine and initialise MapLibre, once. */
function ensureHost(): HTMLElement {
  if (!host) {
    host = document.createElement('div')
    // Starts off-screen (--off) until the first showMap.
    host.className = 'map-host map-host--off'
    const engine = document.createElement('div')
    engine.className = 'map-engine'
    host.appendChild(engine)
    document.body.appendChild(host)
    // Init while off-screen: a translate doesn't change layout size, so MapLibre
    // sizes correctly and begins loading tiles immediately.
    MapView.init(engine)
  }
  return host
}

/** Show the map (creating it on first call). */
export function showMap(): void {
  const h = ensureHost()
  h.classList.remove('map-host--off')
  // Off-screen the map kept its size + GL buffer (we translate, never display:none),
  // so this is just insurance against a layout change while away.
  requestAnimationFrame(() => MapView.invalidateSize())
}

/**
 * Hide the map without destroying it. We translate it off-screen rather than
 * `display:none` it — a hidden (0×0) WebGL canvas drops its drawing buffer and
 * comes back blank; staying display:block keeps the instance rendering.
 */
export function hideMap(): void {
  if (host) host.classList.add('map-host--off')
}
