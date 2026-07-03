/**
 * Attractions overlay controller + pure GeoJSON builders.
 *
 * The map-overlay half of the attractions tier (plans/attractions-plan.md).
 * Unlike the trail/phone layers this one is *viewport-driven, not time-windowed* —
 * POIs are static in time, so `syncAttractions` refetches on map movement
 * (Map.svelte's moveend subscription) rather than following the Selection window.
 *
 * A zoom gate keeps the whole-country dataset legible: below MIN_DETAIL_ZOOM only
 * parks render (6k pins of trailheads at z4 is soup); every selected kind loads
 * once zoomed past it. Kind colors/icons live here so the chrome (DataLayers
 * legend, Nearby panel, detail sheet) shares them without importing the engine.
 */

import type { Feature, FeatureCollection, Point } from 'geojson'

import { getAttractions, type Attraction, type AttractionKind } from './api'
import type { MapView as MapViewType } from './map'

type View = typeof MapViewType

/** Per-kind presentation, in display order (legend, panel filters, sheet header). */
export const KIND_META: { kind: AttractionKind; label: string; icon: string; color: string }[] = [
  { kind: 'park', label: 'Parks', icon: '🏞', color: '#10b981' },
  { kind: 'visitorcenter', label: 'Visitor centers', icon: '🏛', color: '#0ea5e9' },
  { kind: 'campground', label: 'Campgrounds', icon: '⛺', color: '#d97706' },
  { kind: 'tour', label: 'Self-guided tours', icon: '🎧', color: '#6366f1' },
  { kind: 'thingstodo', label: 'Things to do', icon: '🥾', color: '#eab308' },
]

const KIND_BY_KEY = new Map(KIND_META.map((m) => [m.kind, m]))

/** Presentation meta for one kind (falls back to a neutral entry). */
export function kindMeta(kind: string): { label: string; icon: string; color: string } {
  return KIND_BY_KEY.get(kind as AttractionKind) ?? { label: kind, icon: '📍', color: '#94a3b8' }
}

/** Below this zoom only parks render — the whole-country POI set is soup earlier. */
export const MIN_DETAIL_ZOOM = 6

/** The kinds that actually load at a zoom level (parks-only below the gate). */
export function kindsAtZoom(kinds: AttractionKind[], zoom: number): AttractionKind[] {
  if (zoom >= MIN_DETAIL_ZOOM) return kinds
  return kinds.filter((k) => k === 'park')
}

/** Rows → pin features; rows without a coordinate are skipped (they can't pin). */
export function attractionsToFC(rows: Attraction[]): FeatureCollection {
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
export async function syncAttractions(
  view: View,
  bbox: string | null,
  zoom: number,
  kinds: AttractionKind[],
): Promise<string> {
  const mine = ++token
  const effective = kindsAtZoom(kinds, zoom)
  if (!effective.length) {
    view.setAttractionsData(emptyFC())
    return kinds.length ? `Zoom in past z${MIN_DETAIL_ZOOM} for these kinds` : 'No kinds selected'
  }
  const resp = await getAttractions({ bbox: bbox ?? undefined, kinds: effective, limit: 2000 })
  if (mine !== token) return ''
  view.setAttractionsData(attractionsToFC(resp.attractions))
  const gated = effective.length < kinds.length ? ` · zoom in past z${MIN_DETAIL_ZOOM} for more` : ''
  const truncated = resp.truncated ? ' (truncated — zoom in)' : ''
  return `${resp.count.toLocaleString()} places${truncated}${gated}`
}

/** Clear the overlay and cancel any in-flight sync. */
export function clearAttractions(view: View): void {
  token++
  view.setAttractionsData(emptyFC())
}
