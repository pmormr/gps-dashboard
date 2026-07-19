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

import { getPlaces, type Place, type PlaceCategory, type PlaceEvent, type PlaceKind } from './api'
import { emptyFC } from './geo'
import { CATEGORY_META, encodeFeatureId, rowIcon, spriteRef } from './icons'
import { setSuppressedIds } from './labels'
import type { MapView as MapViewType } from './map'

type View = typeof MapViewType

export { CATEGORY_META }

// Detail-pane data-age thresholds (days). Federal rows carry schedule-ish data
// (hours, events) that degrades in weeks; the OSM extract and the GNIS names
// file are seasonal rebuilds (and names barely age), so their banners escalate
// on the slower cadence.
export const STALE_DAYS_FEDERAL = 45
export const STALE_DAYS_BULK = 180

/** Per-kind presentation, in display order (legend, panel filters, sheet header). */
export const KIND_META: { kind: PlaceKind; label: string; icon: string; color: string }[] = [
  { kind: 'park', label: 'Parks', icon: '🏞', color: '#10b981' },
  { kind: 'recarea', label: 'Rec areas', icon: '🌲', color: '#059669' },
  { kind: 'visitorcenter', label: 'Visitor centers', icon: '🏛', color: '#0ea5e9' },
  { kind: 'campground', label: 'Campgrounds', icon: '⛺', color: '#d97706' },
  { kind: 'facility', label: 'Trailheads & sites', icon: '🚩', color: '#14b8a6' },
  { kind: 'tour', label: 'Self-guided tours', icon: '🎧', color: '#6366f1' },
  { kind: 'thingstodo', label: 'Things to do', icon: '🥾', color: '#eab308' },
  { kind: 'site', label: 'Park sites', icon: '🪧', color: '#b45309' },
  { kind: 'permit', label: 'Permits', icon: '🎫', color: '#f43f5e' },
]

const KIND_BY_KEY = new Map(KIND_META.map((m) => [m.kind, m]))

/** Presentation meta for one kind (falls back to a neutral entry). */
export function kindMeta(kind: string): { label: string; icon: string; color: string } {
  return KIND_BY_KEY.get(kind) ?? { label: kind, icon: '📍', color: '#94a3b8' }
}

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
  sprite: string
  label: string
  icon: string
  color: string
  categories: PlaceCategory[]
  defaultOn: boolean
}[] = [
  {
    key: 'nature',
    sprite: 'mountain',
    label: 'Nature',
    icon: '⛰',
    color: '#059669',
    categories: ['park', 'outdoors', 'recreation'],
    defaultOn: true,
  },
  {
    key: 'see',
    sprite: 'attraction',
    label: 'See & do',
    icon: '🎡',
    color: '#eab308',
    categories: ['attraction', 'historic', 'landmark'],
    defaultOn: true,
  },
  {
    key: 'stay',
    sprite: 'campsite',
    label: 'Stay',
    icon: '⛺',
    color: '#d97706',
    categories: ['camping', 'lodging'],
    defaultOn: true,
  },
  {
    key: 'food',
    sprite: 'restaurant',
    label: 'Food & shops',
    icon: '🍽',
    color: '#f97316',
    categories: ['food_drink', 'grocery', 'shopping'],
    defaultOn: true,
  },
  {
    key: 'fuel',
    sprite: 'fuel',
    label: 'Fuel & transport',
    icon: '⛽',
    color: '#f43f5e',
    categories: ['automotive', 'transport'],
    defaultOn: true,
  },
  {
    key: 'towns',
    sprite: 'village',
    label: 'Towns',
    icon: '🏘',
    color: '#a16207',
    categories: ['community'],
    defaultOn: true,
  },
  {
    key: 'services',
    sprite: 'suitcase',
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
 *
 * `offset` is the Map style panel's density slider (-4..0): each step left
 * gates as if one zoom closer, so slider-left genuinely means "more pins,
 * earlier". (The basemap's own marks can't match this — their tiles carry
 * only one zoom of headroom; see basemaps.md — which is why the pin gate is
 * the slider's real lever.) Below z6 the shift is ignored: a continent-scale
 * bbox at rank ≥ 2 is soup and burns the row budget.
 */
export function maxRankForZoom(zoom: number, offset = 0): number {
  const eff = zoom >= 6 ? zoom - offset : zoom
  if (eff >= 14) return 4
  if (eff >= 12) return 3
  if (eff >= 9) return 2
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
        icon: spriteRef(rowIcon(row)),
      },
    })
  }
  return { type: 'FeatureCollection', features }
}

/**
 * Result rows → search-result pin features. No per-kind color: the engine's
 * search layers style them uniformly (accent + halo) so the pushed result set
 * stands out from the category-colored browse overlay — but the icon still
 * names the kind, same language as everything else.
 */
export function searchResultsToFC(rows: Place[]): FeatureCollection {
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
        icon: spriteRef(rowIcon(row)),
      },
    })
  }
  return { type: 'FeatureCollection', features }
}

/**
 * Tile feature ids for the OSM rows in a drawn set — the twin-suppression
 * feed: a basemap mark whose feature is already drawn as a pin must not
 * render twice. Absorbed twins count: a grouped-out OSM row's mark would
 * otherwise reappear beside the richer pin that replaced it.
 */
export function suppressionIds(rows: Place[]): number[] {
  const ids: number[] = []
  const push = (source: string, sourceId: string): void => {
    if (source !== 'osm') return
    const fid = encodeFeatureId(sourceId)
    if (fid != null) ids.push(fid)
  }
  for (const row of rows) {
    push(row.source, row.source_id)
    for (const twin of row.twins ?? []) push(twin.source, twin.source_id)
  }
  return ids
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

/**
 * One-line label for an event's first occurrence: `date[ sep time][ (+N[ more])]`.
 * The compact list form omits "more" (`+N`); the detail sheet includes it and
 * uses a middot time separator.
 */
export function eventDateLabel(
  ev: PlaceEvent,
  { timeSep = ' ', moreSuffix = false }: { timeSep?: string; moreSuffix?: boolean } = {},
): string {
  const first = ev.dates[0]
  if (!first) return ''
  const time = first.time_start ? `${timeSep}${first.time_start}` : ''
  const extra = ev.dates.length - 1
  const more = extra > 0 ? ` (+${extra}${moreSuffix ? ' more' : ''})` : ''
  return `${first.date}${time}${more}`
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
  densityOffset = 0,
): Promise<string> {
  const mine = ++token
  inflight?.abort()
  if (!categories.length) {
    view.setPlacesData(emptyFC())
    return 'No groups selected'
  }
  const controller = new AbortController()
  inflight = controller
  const maxRank = maxRankForZoom(zoom, densityOffset)
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
  setSuppressedIds(view, 'browse', suppressionIds(resp.places))
  const gated = maxRank < 4 ? ' · zoom in for more' : ''
  const truncated = resp.truncated ? ' (truncated — zoom in)' : ''
  return `${resp.count.toLocaleString()} places${truncated}${gated}`
}

/** Clear the overlay (and its mark suppression) and cancel any in-flight sync. */
export function clearPlaces(view: View): void {
  token++
  inflight?.abort()
  view.setPlacesData(emptyFC())
  setSuppressedIds(view, 'browse', [])
}
