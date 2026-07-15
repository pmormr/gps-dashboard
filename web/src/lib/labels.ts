/**
 * Basemap `pois` layer composer — the vector base's POI marks.
 *
 * One function owns the layer's live GL state (filter, icons, colors), fed by
 * three writers: the Map style panel (text density + minor roads), the shared
 * POI category selection (the same category groups that filter the places
 * overlay — one control, plan decision), and the overlay's twin-suppression
 * ids (a mark whose tier row is currently drawn as a pin must not render
 * twice). Icons and colors come from the unified language in `icons.ts`
 * (`poi` SDF sprite + category colors), so basemap marks and tier pins read
 * as one system.
 *
 * Runtime-MapLibre-free: operates on the `gl` map handed over by the
 * `MapView` façade, so importing it doesn't pull MapLibre into a bundle.
 */

import type { ExpressionSpecification, Map as MlMap } from 'maplibre-gl'

import type { MapView as MapViewType } from './map'
import { BASEMAP_KINDS, spriteRef } from './icons'
import { CATEGORY_META } from './places'

type View = typeof MapViewType

/** The generated vector-basemap themes (web/scripts/generate-basemap-styles.mjs). */
export type BasemapTheme = 'light' | 'dark' | 'white' | 'grayscale' | 'black'

/** Panel-facing theme list, in display order. */
export const BASEMAP_THEMES: { id: BasemapTheme; label: string }[] = [
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
  { id: 'white', label: 'White' },
  { id: 'grayscale', label: 'Grayscale' },
  { id: 'black', label: 'Black' },
]

/** The boot theme — the store's initial state and the engine's warm fetch. */
export const DEFAULT_BASEMAP_THEME: BasemapTheme = 'dark'

/** Themes with a dark ground — marks swap to a dark halo so they don't glow. */
const DARK_THEMES: ReadonlySet<BasemapTheme> = new Set(['dark', 'black'])

/** Current style-panel settings (a snapshot the store exposes). */
export interface LabelSettings {
  offset: number
  minorRoads: boolean
  theme: BasemapTheme
}

const CATEGORY_COLOR = new Map(CATEGORY_META.map((m) => [m.category as string, m.color]))
/** Mark color for kinds whose category has no meta (should not happen) — a mid-grey that reads on light and dark grounds. */
const NEUTRAL = '#8a919c'

// Composer inputs beyond the style panel: the shared category selection and
// the overlay's suppression ids, each pushed by its own writer.
let poiCategories: ReadonlySet<string> = new Set(CATEGORY_META.map((m) => m.category as string))
const suppressed: { browse: number[]; search: number[] } = { browse: [], search: [] }
let getSettings: (() => LabelSettings) | null = null

/** The kinds visible under the current category selection. */
function visibleKinds(): string[] {
  return Object.keys(BASEMAP_KINDS).filter((k) => poiCategories.has(BASEMAP_KINDS[k].category))
}

/** kind → sprite ref for every mapped kind (unmapped kinds never pass the filter). */
function iconExpression(): ExpressionSpecification {
  const pairs = Object.entries(BASEMAP_KINDS).flatMap(([k, m]) => [k, spriteRef(m.icon)])
  // The spread defeats TS's tuple checking for match expressions — the shape
  // is (input, ...label/output pairs, fallback), which this builds by hand.
  return ['match', ['get', 'kind'], ...pairs, spriteRef('marker')] as unknown as ExpressionSpecification
}

/** kind → its category color (icons and text share it). */
function colorExpression(): ExpressionSpecification {
  const pairs = Object.entries(BASEMAP_KINDS).flatMap(([k, m]) => [
    k,
    CATEGORY_COLOR.get(m.category) ?? NEUTRAL,
  ])
  return ['match', ['get', 'kind'], ...pairs, NEUTRAL] as unknown as ExpressionSpecification
}

/**
 * Push the composed state into a GL map: filter the `pois` layer to the
 * selected categories' kinds gated by the density offset minus any suppressed
 * twin ids, restyle its icons/colors to the unified language, and gate
 * minor-road labels. Idempotent and safe to call on every style (re)load; a
 * no-op until the style + `pois` layer exist.
 */
export function applyLabels(gl: MlMap, s: LabelSettings): void {
  if (!gl.isStyleLoaded() || !gl.getLayer('pois')) return
  const clauses: ExpressionSpecification[] = [
    ['in', ['get', 'kind'], ['literal', visibleKinds()]] as ExpressionSpecification,
    ['>=', ['zoom'], ['+', ['get', 'min_zoom'], s.offset]] as ExpressionSpecification,
  ]
  const ids = [...suppressed.browse, ...suppressed.search]
  if (ids.length) {
    clauses.push(['!', ['in', ['id'], ['literal', ids]]] as ExpressionSpecification)
  }
  const halo = DARK_THEMES.has(s.theme) ? '#14181c' : '#ffffff'
  try {
    gl.setFilter('pois', ['all', ...clauses] as ExpressionSpecification)
    gl.setLayerZoomRange('roads_labels_minor', s.minorRoads ? 13 : 24, 24)
    gl.setLayoutProperty('pois', 'icon-image', iconExpression())
    gl.setPaintProperty('pois', 'icon-color', colorExpression())
    gl.setPaintProperty('pois', 'icon-halo-color', halo)
    gl.setPaintProperty('pois', 'icon-halo-width', 1)
    gl.setPaintProperty('pois', 'text-color', colorExpression())
    gl.setPaintProperty('pois', 'text-halo-color', halo)
  } catch (e) {
    console.error('label controls:', e)
  }
}

/**
 * Wire label application to the vector base: re-apply the composed state
 * whenever the vector style (re)loads. `onVectorBase` stores a single callback
 * (latest wins), so calling this on each Map mount is safe — no listener
 * accumulation. The stored settings getter also serves the other writers'
 * re-applies (categories / suppression pushes).
 */
export function hookLabels(view: View, settings: () => LabelSettings): void {
  getSettings = settings
  view.onVectorBase((gl) => applyLabels(gl, settings()))
}

/** Re-apply to the current vector base now (on a panel change). No-op on raster. */
export function reapply(view: View, settings: LabelSettings): void {
  const gl = view.getVectorBase()
  if (gl) applyLabels(gl, settings)
}

/** Push the shared POI category selection (the places-overlay group chips). */
export function setPoiCategories(view: View, categories: ReadonlySet<string>): void {
  poiCategories = categories
  if (getSettings) reapply(view, getSettings())
}

/**
 * Push twin-suppression ids: tile feature ids whose tier rows the overlay is
 * currently drawing (browse pins and search results are independent writers).
 */
export function setSuppressedIds(view: View, key: 'browse' | 'search', ids: number[]): void {
  suppressed[key] = ids
  if (getSettings) reapply(view, getSettings())
}
