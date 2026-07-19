/**
 * Phone-history overlay controller + pure GeoJSON builders.
 *
 * The map-overlay half of the phone location-history tier (plans/phone-history-
 * plan.md). Unlike the drone overlay (fetch-all-once), the phone breadcrumb is 15
 * years deep, so it *follows the global time selection*: `syncPhone` refetches the
 * current window and hands prebuilt GeoJSON to the MapView façade.
 *
 * Color-by-mode and the run-splitting live here (pure, unit-tested); each emitted
 * line feature carries its own `color`, so map.ts stays domain-free and just paints
 * `['get','color']`. This module imports no MapLibre, so the chrome (Layers panel)
 * can import the legend from it without pulling the map engine into the main bundle.
 */

import type { Feature, FeatureCollection, LineString, Point } from 'geojson'

import { getPhonePlaces, getPhoneTracks, type PhonePath, type PhoneVisit } from './api'
import { emptyFC, escapeHtml, fmtDate, fmtDurationSecs, fmtTime } from './geo'
import type { MapView as MapViewType } from './map'

type View = typeof MapViewType

// Raw Timeline activity types collapse to a handful of mode groups; each group gets
// one color. Keep the palette in sync with the Layers-panel legend (MODE_LEGEND).
const MODE_GROUP: Record<string, string> = {
  IN_PASSENGER_VEHICLE: 'driving',
  IN_VEHICLE: 'driving',
  IN_TAXI: 'driving',
  WALKING: 'walking',
  ON_FOOT: 'walking',
  RUNNING: 'walking',
  HIKING: 'walking',
  CYCLING: 'cycling',
  IN_TRAIN: 'transit',
  IN_SUBWAY: 'transit',
  IN_BUS: 'transit',
  IN_FERRY: 'transit',
  IN_TRAM: 'transit',
  FLYING: 'flying',
}

export const MODE_COLORS: Record<string, string> = {
  driving: '#3b82f6',
  walking: '#22c55e',
  cycling: '#f59e0b',
  transit: '#14b8a6',
  flying: '#ec4899',
  unknown: '#94a3b8',
}

/** Legend rows for the Layers panel, in display order. */
export const MODE_LEGEND: { group: string; label: string }[] = [
  { group: 'driving', label: 'Driving' },
  { group: 'walking', label: 'Walking' },
  { group: 'cycling', label: 'Cycling' },
  { group: 'transit', label: 'Transit' },
  { group: 'flying', label: 'Flying' },
  { group: 'unknown', label: 'Unknown' },
]

const VISIT_COLOR = '#e2e8f0'

/** Map a raw activity type (or null) to its color-group key. */
export function modeGroup(activityType: string | null | undefined): string {
  return (activityType && MODE_GROUP[activityType]) || 'unknown'
}

function pushRun(
  features: Feature<LineString>[],
  points: { lon: number; lat: number }[],
  group: string,
): void {
  if (points.length < 2) return
  features.push({
    type: 'Feature',
    geometry: { type: 'LineString', coordinates: points.map((p) => [p.lon, p.lat]) },
    properties: { group, color: MODE_COLORS[group] ?? MODE_COLORS.unknown },
  })
}

/**
 * Split each path into contiguous same-mode runs → one colored LineString per run.
 *
 * MapLibre colors a line per-feature, not per-vertex, so color-by-mode means one
 * feature per run. Adjacent runs share their boundary vertex (`slice(start, i + 1)`)
 * so the polyline stays visually connected across a mode change.
 */
export function phonePathsToFC(paths: PhonePath[]): FeatureCollection {
  const features: Feature<LineString>[] = []
  for (const path of paths) {
    const pts = path.points ?? []
    if (pts.length < 2) continue
    let start = 0
    let group = modeGroup(pts[0].activity_type)
    for (let i = 1; i < pts.length; i++) {
      const g = modeGroup(pts[i].activity_type)
      if (g !== group) {
        pushRun(features, pts.slice(start, i + 1), group)
        start = i
        group = g
      }
    }
    pushRun(features, pts.slice(start), group)
  }
  return { type: 'FeatureCollection', features }
}

function visitPopupHtml(visit: PhoneVisit): string {
  const label =
    visit.semantic_type && visit.semantic_type !== 'UNKNOWN' ? visit.semantic_type : 'Place visit'
  const durMs = new Date(visit.end_time).getTime() - new Date(visit.start_time).getTime()
  return (
    `<div class="phone-popup">` +
    `<div class="phone-popup-title">${escapeHtml(label)}</div>` +
    `<div class="phone-popup-meta">${fmtDate(visit.start_time)} · ` +
    `${fmtTime(visit.start_time)} → ${fmtTime(visit.end_time)}</div>` +
    `<div class="phone-popup-meta">Stayed ${fmtDurationSecs(durMs / 1000)}</div>` +
    `</div>`
  )
}

/** Build the visit-pin FeatureCollection (each carries its color + prebuilt popup). */
export function phoneVisitsToFC(visits: PhoneVisit[]): FeatureCollection {
  const features: Feature<Point>[] = visits.map((v) => ({
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [v.lon, v.lat] },
    properties: { color: VISIT_COLOR, popup: visitPopupHtml(v) },
  }))
  return { type: 'FeatureCollection', features }
}


// A monotonic token drops a stale fetch: while scrubbing, an earlier window's
// response must not overwrite a later one's (the same guard the trail uses).
let token = 0

/**
 * Fetch the phone history for a window and push it to the map. Returns a status
 * string for the panel; a superseded fetch returns '' and paints nothing.
 */
export async function syncPhone(view: View, fromIso: string, toIso: string): Promise<string> {
  const mine = ++token
  const [tracks, places] = await Promise.all([
    getPhoneTracks(fromIso, toIso),
    getPhonePlaces(fromIso, toIso),
  ])
  if (mine !== token) return ''
  const paths = tracks.paths ?? []
  const visits = places.visits ?? []
  view.setPhoneData(phonePathsToFC(paths), phoneVisitsToFC(visits))
  const nPoints = paths.reduce((sum, p) => sum + (p.points?.length ?? 0), 0)
  return nPoints
    ? `${nPoints.toLocaleString()} pts · ${visits.length} visits`
    : 'No history in this window'
}

/** Clear the overlay and cancel any in-flight sync. */
export function clearPhone(view: View): void {
  token++
  view.setPhoneData(emptyFC(), emptyFC())
}
