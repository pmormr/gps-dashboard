/**
 * Places overlay controller + pure GeoJSON builders.
 *
 * The map-overlay half of the places tier (.claude/modules/places.md).
 * Unlike the trail/phone layers this one is *viewport-driven, not time-windowed* —
 * POIs are static in time, so `syncPlaces` refetches on map movement
 * (Map.svelte's moveend subscription) rather than following the Selection window.
 *
 * A zoom gate keeps the whole-country dataset legible: below MIN_DETAIL_ZOOM only
 * the container kinds render (parks + RIDB rec areas — 16k pins of trailheads at
 * z4 is soup); every selected kind loads once zoomed past it. Kind colors/icons
 * live here so the chrome (DataLayers legend, Nearby panel, detail sheet) shares
 * them without importing the engine.
 */

import type { Feature, FeatureCollection, Point } from 'geojson'

import { getPlaces, type Place, type PlaceCategory, type PlaceKind } from './api'
import type { MapView as MapViewType } from './map'

type View = typeof MapViewType

/** Per-kind presentation, in display order (legend, panel filters, sheet header). */
export const KIND_META: { kind: PlaceKind; label: string; icon: string; color: string }[] = [
  { kind: 'park', label: 'Parks', icon: '🏞', color: '#10b981' },
  { kind: 'recarea', label: 'Rec areas', icon: '🌲', color: '#059669' },
  { kind: 'visitorcenter', label: 'Visitor centers', icon: '🏛', color: '#0ea5e9' },
  { kind: 'campground', label: 'Campgrounds', icon: '⛺', color: '#d97706' },
  { kind: 'facility', label: 'Trailheads & sites', icon: '🚩', color: '#14b8a6' },
  { kind: 'tour', label: 'Self-guided tours', icon: '🎧', color: '#6366f1' },
  { kind: 'thingstodo', label: 'Things to do', icon: '🥾', color: '#eab308' },
  { kind: 'permit', label: 'Permits', icon: '🎫', color: '#f43f5e' },
]

const KIND_BY_KEY = new Map(KIND_META.map((m) => [m.kind, m]))

/** Presentation meta for one kind (falls back to a neutral entry). */
export function kindMeta(kind: string): { label: string; icon: string; color: string } {
  return KIND_BY_KEY.get(kind) ?? { label: kind, icon: '📍', color: '#94a3b8' }
}

/**
 * Per-category presentation (the unified taxonomy, plan decisions 11+15), in
 * display order — the Places view's browse chips and the fallback meta for
 * OSM rows, whose open-ended kinds ('amenity=cafe') have no per-kind entry.
 */
export const CATEGORY_META: {
  category: PlaceCategory
  label: string
  icon: string
  color: string
}[] = [
  { category: 'park', label: 'Parks', icon: '🏞', color: '#10b981' },
  { category: 'outdoors', label: 'Outdoors', icon: '⛰', color: '#059669' },
  { category: 'camping', label: 'Camping', icon: '⛺', color: '#d97706' },
  { category: 'attraction', label: 'Attractions', icon: '🎡', color: '#eab308' },
  { category: 'historic', label: 'Historic', icon: '🏛', color: '#b45309' },
  { category: 'landmark', label: 'Landmarks', icon: '🗼', color: '#8b5cf6' },
  { category: 'recreation', label: 'Recreation', icon: '⚽', color: '#14b8a6' },
  { category: 'lodging', label: 'Lodging', icon: '🛏', color: '#6366f1' },
  { category: 'food_drink', label: 'Food & drink', icon: '🍽', color: '#f97316' },
  { category: 'grocery', label: 'Grocery', icon: '🛒', color: '#84cc16' },
  { category: 'shopping', label: 'Shopping', icon: '🛍', color: '#ec4899' },
  { category: 'automotive', label: 'Fuel & auto', icon: '⛽', color: '#f43f5e' },
  { category: 'transport', label: 'Transport', icon: '🚌', color: '#0ea5e9' },
  { category: 'health', label: 'Health', icon: '🏥', color: '#ef4444' },
  { category: 'emergency', label: 'Emergency', icon: '🚨', color: '#dc2626' },
  { category: 'community', label: 'Communities', icon: '🏘', color: '#a16207' },
  { category: 'civic', label: 'Civic', icon: '🏫', color: '#64748b' },
  { category: 'services', label: 'Services', icon: '🏦', color: '#94a3b8' },
  { category: 'utility', label: 'Utilities', icon: '🚰', color: '#78716c' },
]

const CATEGORY_BY_KEY = new Map(CATEGORY_META.map((m) => [m.category as string, m]))

/**
 * Browse-level grouping of the categories — 19 chips wrapped to three rows on
 * a phone, so the filter UI works in groups (one row) and fine-grained
 * refinement happens through kind facets instead of per-category toggles.
 * Every CATEGORY_META category belongs to exactly one group (tested); group
 * colors reuse a representative member's so pins and legend stay related.
 * `defaultOn: false` marks browse noise (infrastructure/civic) that stays one
 * tap away.
 */
export const CATEGORY_GROUPS: {
  key: string
  label: string
  icon: string
  color: string
  categories: PlaceCategory[]
  defaultOn: boolean
}[] = [
  {
    key: 'nature',
    label: 'Nature',
    icon: '⛰',
    color: '#059669',
    categories: ['park', 'outdoors', 'recreation'],
    defaultOn: true,
  },
  {
    key: 'see',
    label: 'See & do',
    icon: '🎡',
    color: '#eab308',
    categories: ['attraction', 'historic', 'landmark'],
    defaultOn: true,
  },
  {
    key: 'stay',
    label: 'Stay',
    icon: '⛺',
    color: '#d97706',
    categories: ['camping', 'lodging'],
    defaultOn: true,
  },
  {
    key: 'food',
    label: 'Food & shops',
    icon: '🍽',
    color: '#f97316',
    categories: ['food_drink', 'grocery', 'shopping'],
    defaultOn: true,
  },
  {
    key: 'fuel',
    label: 'Fuel & transport',
    icon: '⛽',
    color: '#f43f5e',
    categories: ['automotive', 'transport'],
    defaultOn: true,
  },
  {
    key: 'towns',
    label: 'Towns',
    icon: '🏘',
    color: '#a16207',
    categories: ['community'],
    defaultOn: true,
  },
  {
    key: 'services',
    label: 'Services',
    icon: '🏦',
    color: '#94a3b8',
    categories: ['services', 'health', 'emergency', 'civic', 'utility'],
    defaultOn: false,
  },
]

/** Group keys in display order (the all-on default for the map overlay). */
export const ALL_GROUP_KEYS = CATEGORY_GROUPS.map((g) => g.key)

/** Expand selected group keys to their member categories (the API's axis). */
export function expandGroups(keys: ReadonlySet<string>): PlaceCategory[] {
  return CATEGORY_GROUPS.filter((g) => keys.has(g.key)).flatMap((g) => g.categories)
}

/** Presentation meta for one category (neutral fallback for unmapped rows). */
export function categoryMeta(category: string | null): {
  label: string
  icon: string
  color: string
} {
  return (
    (category && CATEGORY_BY_KEY.get(category)) || { label: 'Other', icon: '📍', color: '#94a3b8' }
  )
}

/**
 * Human label for one facet kind: federal kinds use their per-kind meta; OSM's
 * open-ended kinds ('amenity=cafe') humanize the tag value ('Cafe').
 */
export function facetLabel(kind: string): string {
  const federal = KIND_BY_KEY.get(kind)
  if (federal) return federal.label
  const value = kind.includes('=') ? kind.split('=', 2)[1] : kind
  const text = value.replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/**
 * Presentation for one row: the federal kinds keep their richer per-kind meta;
 * everything else (OSM's open-ended kinds) falls back to its category's.
 */
export function placeMeta(row: { source_kind: string; category: string | null }): {
  label: string
  icon: string
  color: string
} {
  return KIND_BY_KEY.get(row.source_kind) ?? categoryMeta(row.category)
}

/**
 * The rank×zoom pin gate (plan decisions 11+13): each zoom admits ranks down
 * to its tier — 1 = major destination (any zoom), 2 ~z9+, 3 ~z12+, 4 ~z14+.
 * Rank 5 (micro furniture) is search-only, never auto-pinned; one gate
 * governs every source, since NPS/RIDB rows carry ranks from the same scale.
 */
export function maxRankForZoom(zoom: number): number {
  if (zoom >= 14) return 4
  if (zoom >= 12) return 3
  if (zoom >= 9) return 2
  return 1
}

/** Rows → pin features; rows without a coordinate are skipped (they can't pin). */
export function placesToFC(rows: Place[]): FeatureCollection {
  const features: Feature<Point>[] = []
  for (const row of rows) {
    if (row.lat == null || row.lon == null) continue
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [row.lon, row.lat] },
      properties: {
        id: row.id,
        kind: row.source_kind,
        name: row.name,
        color: placeMeta(row).color,
      },
    })
  }
  return { type: 'FeatureCollection', features }
}

/**
 * Result rows → search-result pin features. No per-kind color: the engine's
 * search layers style them uniformly (accent + halo) so the pushed result set
 * stands out from the category-colored browse overlay.
 */
export function searchResultsToFC(rows: Place[]): FeatureCollection {
  const features: Feature<Point>[] = []
  for (const row of rows) {
    if (row.lat == null || row.lon == null) continue
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [row.lon, row.lat] },
      properties: { id: row.id, kind: row.source_kind, name: row.name },
    })
  }
  return { type: 'FeatureCollection', features }
}

/**
 * Flatten NPS rich-text to plain text: drop tags, decode the common entities,
 * collapse whitespace. Event descriptions arrive as HTML; the app renders them
 * as text rather than injecting source-controlled markup.
 */
export function stripHtml(html: string): string {
  return html
    .replace(/<[^>]*>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#0?39;/g, "'")
    .replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ')
    .replace(/ ([.,;:!?])/g, '$1')
    .trim()
}

function emptyFC(): FeatureCollection {
  return { type: 'FeatureCollection', features: [] }
}

// A monotonic token drops a stale fetch: while panning, an earlier viewport's
// response must not overwrite a later one's (the same guard the phone layer
// uses). The AbortController goes further and kills the superseded transfer
// itself — a 2000-row payload is ~0.4 s of HaLow bandwidth per pan otherwise.
let token = 0
let inflight: AbortController | null = null

/**
 * Fetch the POIs for the current viewport and push them to the map. The
 * rank×zoom gate bounds every read — at 10.7M broad rows "all pins in view"
 * is never a valid shape. Returns a status string for the panel; a
 * superseded fetch returns '' and paints nothing.
 */
export async function syncPlaces(
  view: View,
  bbox: string | null,
  zoom: number,
  categories: PlaceCategory[],
): Promise<string> {
  const mine = ++token
  inflight?.abort()
  if (!categories.length) {
    view.setPlacesData(emptyFC())
    return 'No groups selected'
  }
  const controller = new AbortController()
  inflight = controller
  const maxRank = maxRankForZoom(zoom)
  let resp
  try {
    resp = await getPlaces({
      bbox: bbox ?? undefined,
      // All-on means unfiltered — that also keeps unmapped-category rows.
      categories: categories.length === CATEGORY_META.length ? undefined : categories,
      maxRank,
      limit: 2000,
      signal: controller.signal,
    })
  } catch (err) {
    if (controller.signal.aborted) return ''
    throw err
  }
  if (mine !== token) return ''
  view.setPlacesData(placesToFC(resp.places))
  const gated = maxRank < 4 ? ' · zoom in for more' : ''
  const truncated = resp.truncated ? ' (truncated — zoom in)' : ''
  return `${resp.count.toLocaleString()} places${truncated}${gated}`
}

/** Clear the overlay and cancel any in-flight sync. */
export function clearPlaces(view: View): void {
  token++
  inflight?.abort()
  view.setPlacesData(emptyFC())
}
