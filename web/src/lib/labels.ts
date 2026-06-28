/**
 * Label / POI density controls for the vector basemap (ported from labels.js).
 *
 * Drives the live MapLibre GL style of the map's vector base directly — settings
 * are global (one panel applies to the vector base and is re-applied whenever its
 * style reloads, e.g. after a base swap). This module is runtime-MapLibre-free: it
 * operates on the `gl` map handed to it by the `MapView` façade, so importing it
 * doesn't pull MapLibre into a bundle.
 */

import type { ExpressionSpecification, Map as MlMap } from 'maplibre-gl'

import type { MapView as MapViewType } from './map'

type View = typeof MapViewType

/** Current label-panel settings (a snapshot the store exposes). */
export interface LabelSettings {
  groups: ReadonlySet<string>
  offset: number
  minorRoads: boolean
}

/** POI category → the `kind` values it surfaces in the Protomaps schema. */
export const POI_GROUPS: Record<string, string[]> = {
  Fuel: ['fuel'],
  Lodging: ['hotel', 'motel', 'hostel', 'guest_house'],
  Food: ['restaurant', 'cafe', 'fast_food', 'bar', 'pub', 'brewery'],
  Medical: ['hospital', 'clinic', 'doctors', 'pharmacy', 'dentist'],
  Parking: ['parking'],
  Shops: ['supermarket', 'convenience', 'mall', 'marketplace'],
  Outdoors: ['park', 'peak', 'viewpoint', 'camp_site', 'picnic_site', 'beach', 'information'],
  Civic: [
    'library', 'museum', 'post_office', 'townhall', 'school',
    'university', 'college', 'place_of_worship', 'theatre', 'bank',
  ],
}

// The shipped style's pois `text-color` is a case on `kind` with no branch for the
// kinds we surface (fuel, hospital, …), so they fall to the #e2dfda fallback —
// identical to text-halo-color, i.e. invisible. Recolor only that fallback to a
// readable dark; covered kinds keep their category colors.
const READABLE_FALLBACK = '#3a3a3a'

/**
 * Push the current settings into a GL map: widen the `pois` layer to the surfaced
 * kinds gated by the density offset, gate minor-road labels, and fix the invisible
 * fallback color. Idempotent and safe to call on every style (re)load; a no-op
 * until the style + `pois` layer exist.
 */
export function applyLabels(gl: MlMap, s: LabelSettings): void {
  if (!gl.isStyleLoaded() || !gl.getLayer('pois')) return
  const kinds = new Set<string>()
  for (const g of s.groups) for (const k of POI_GROUPS[g] ?? []) kinds.add(k)
  try {
    gl.setFilter('pois', [
      'all',
      ['in', ['get', 'kind'], ['literal', [...kinds]]],
      ['>=', ['zoom'], ['+', ['get', 'min_zoom'], s.offset]],
    ] as ExpressionSpecification)
    gl.setLayerZoomRange('roads_labels_minor', s.minorRoads ? 13 : 24, 24)
    const tc = gl.getPaintProperty('pois', 'text-color')
    if (Array.isArray(tc) && tc[tc.length - 1] !== READABLE_FALLBACK) {
      const fixed = tc.slice()
      fixed[fixed.length - 1] = READABLE_FALLBACK
      gl.setPaintProperty('pois', 'text-color', fixed)
    }
  } catch (e) {
    console.error('label controls:', e)
  }
}

/**
 * Wire label application to the vector base: re-apply current settings whenever the
 * vector style (re)loads. `onVectorBase` stores a single callback (latest wins), so
 * calling this on each Map mount is safe — no listener accumulation.
 */
export function hookLabels(view: View, getSettings: () => LabelSettings): void {
  view.onVectorBase((gl) => applyLabels(gl, getSettings()))
}

/** Re-apply settings to the current vector base now (on a panel change). No-op on raster. */
export function reapply(view: View, settings: LabelSettings): void {
  const gl = view.getVectorBase()
  if (gl) applyLabels(gl, settings)
}
