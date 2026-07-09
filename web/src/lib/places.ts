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

import { getPlaces, type Place, type PlaceKind } from './api'
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
  return KIND_BY_KEY.get(kind as PlaceKind) ?? { label: kind, icon: '📍', color: '#94a3b8' }
}

/** Below this zoom only containers render — the whole-country POI set is soup earlier. */
export const MIN_DETAIL_ZOOM = 6

/** The container kinds still shown below the zoom gate (parks + their RIDB analogs). */
const CONTAINER_KINDS: ReadonlySet<PlaceKind> = new Set(['park', 'recarea'])

/** The kinds that actually load at a zoom level (containers-only below the gate). */
export function kindsAtZoom(kinds: PlaceKind[], zoom: number): PlaceKind[] {
  if (zoom >= MIN_DETAIL_ZOOM) return kinds
  return kinds.filter((k) => CONTAINER_KINDS.has(k))
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
        color: kindMeta(row.source_kind).color,
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
 * Fetch the POIs for the current viewport and push them to the map. Returns a
 * status string for the panel; a superseded fetch returns '' and paints nothing.
 */
export async function syncPlaces(
  view: View,
  bbox: string | null,
  zoom: number,
  kinds: PlaceKind[],
): Promise<string> {
  const mine = ++token
  const effective = kindsAtZoom(kinds, zoom)
  if (!effective.length) {
    view.setPlacesData(emptyFC())
    return kinds.length ? `Zoom in past z${MIN_DETAIL_ZOOM} for these kinds` : 'No kinds selected'
  }
  const resp = await getPlaces({ bbox: bbox ?? undefined, kinds: effective, limit: 2000 })
  if (mine !== token) return ''
  view.setPlacesData(placesToFC(resp.places))
  const gated = effective.length < kinds.length ? ` · zoom in past z${MIN_DETAIL_ZOOM} for more` : ''
  const truncated = resp.truncated ? ' (truncated — zoom in)' : ''
  return `${resp.count.toLocaleString()} places${truncated}${gated}`
}

/** Clear the overlay and cancel any in-flight sync. */
export function clearPlaces(view: View): void {
  token++
  view.setPlacesData(emptyFC())
}
