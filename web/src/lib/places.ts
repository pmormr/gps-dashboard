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
  { category: 'civic', label: 'Civic', icon: '🏫', color: '#64748b' },
  { category: 'services', label: 'Services', icon: '🏦', color: '#94a3b8' },
  { category: 'utility', label: 'Utilities', icon: '🚰', color: '#78716c' },
]

const CATEGORY_BY_KEY = new Map(CATEGORY_META.map((m) => [m.category as string, m]))

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
// response must not overwrite a later one's (the same guard the phone layer uses).
let token = 0

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
  if (!categories.length) {
    view.setPlacesData(emptyFC())
    return 'No categories selected'
  }
  const maxRank = maxRankForZoom(zoom)
  const resp = await getPlaces({
    bbox: bbox ?? undefined,
    // All-on means unfiltered — that also keeps unmapped-category rows.
    categories: categories.length === CATEGORY_META.length ? undefined : categories,
    maxRank,
    limit: 2000,
  })
  if (mine !== token) return ''
  view.setPlacesData(placesToFC(resp.places))
  const gated = maxRank < 4 ? ' · zoom in for more' : ''
  const truncated = resp.truncated ? ' (truncated — zoom in)' : ''
  return `${resp.count.toLocaleString()} places${truncated}${gated}`
}

/** Clear the overlay and cancel any in-flight sync. */
export function clearPlaces(view: View): void {
  token++
  view.setPlacesData(emptyFC())
}
