/**
 * The one map-icon language (plans/poi-map-unification-plan.md).
 *
 * Sprite icon names come from the `poi` multi-sprite —
 * `static/vendor/basemap/sprite-poi/`, built by `tools/build_poi_sprite.py`
 * from the vendored Maki/Temaki SVGs (`static/vendor/poi-icons/svg/`, CC0).
 * The basemap `pois` layer, the tier pin layers, and the HTML UI all resolve
 * icons through these tables, so the mark you tap on the map is the icon in
 * the list and the sheet. The vitest suite checks every name referenced here
 * exists in the committed sprite JSON.
 *
 * Also here: the planetiler feature-id codec — basemap `pois` features carry
 * `type·2⁴⁴ + osm_id`, which round-trips to the tier's `source_id` strings
 * (`node/…`/`way/…`/`relation/…`), the bridge tap-through and twin
 * suppression ride on.
 */

import type { PlaceCategory } from './api'

/**
 * Per-category presentation (the unified taxonomy, plan decisions 11+15), in
 * display order — the browse chips, the pin/mark colors, and the fallback
 * meta for OSM rows, whose open-ended kinds ('amenity=cafe') have no per-kind
 * entry. Lives here (not places.ts) so the basemap composer can import it
 * without a module cycle; places.ts re-exports it for the views.
 */
export const CATEGORY_META: {
  category: PlaceCategory
  label: string
  icon: string
  sprite: string
  color: string
}[] = [
  { category: 'park', label: 'Parks', icon: '🏞', sprite: 'park', color: '#10b981' },
  { category: 'outdoors', label: 'Outdoors', icon: '⛰', sprite: 'mountain', color: '#059669' },
  { category: 'camping', label: 'Camping', icon: '⛺', sprite: 'campsite', color: '#d97706' },
  { category: 'attraction', label: 'Attractions', icon: '🎡', sprite: 'attraction', color: '#eab308' },
  { category: 'historic', label: 'Historic', icon: '🏛', sprite: 'monument', color: '#b45309' },
  { category: 'landmark', label: 'Landmarks', icon: '🗼', sprite: 'landmark', color: '#8b5cf6' },
  { category: 'recreation', label: 'Recreation', icon: '⚽', sprite: 'pitch', color: '#14b8a6' },
  { category: 'lodging', label: 'Lodging', icon: '🛏', sprite: 'lodging', color: '#6366f1' },
  { category: 'food_drink', label: 'Food & drink', icon: '🍽', sprite: 'restaurant', color: '#f97316' },
  { category: 'grocery', label: 'Grocery', icon: '🛒', sprite: 'grocery', color: '#84cc16' },
  { category: 'shopping', label: 'Shopping', icon: '🛍', sprite: 'shop', color: '#ec4899' },
  { category: 'automotive', label: 'Fuel & auto', icon: '⛽', sprite: 'car', color: '#f43f5e' },
  { category: 'transport', label: 'Transport', icon: '🚌', sprite: 'bus', color: '#0ea5e9' },
  { category: 'health', label: 'Health', icon: '🏥', sprite: 'hospital', color: '#ef4444' },
  { category: 'emergency', label: 'Emergency', icon: '🚨', sprite: 'emergency-phone', color: '#dc2626' },
  { category: 'community', label: 'Communities', icon: '🏘', sprite: 'village', color: '#a16207' },
  { category: 'civic', label: 'Civic', icon: '🏫', sprite: 'town-hall', color: '#64748b' },
  { category: 'services', label: 'Services', icon: '🏦', sprite: 'suitcase', color: '#94a3b8' },
  { category: 'utility', label: 'Utilities', icon: '🚰', sprite: 'drinking-water', color: '#78716c' },
]

/** Tier category → sprite icon (derived; the OSM rows' axis + HTML fallback). */
export const CATEGORY_ICONS: Record<string, string> = Object.fromEntries(
  CATEGORY_META.map((m) => [m.category as string, m.sprite]),
)

/** Federal source_kind → sprite icon (richer than the category fallback). */
export const FEDERAL_KIND_ICONS: Record<string, string> = {
  park: 'park',
  recarea: 'park-alt1',
  visitorcenter: 'ranger-station',
  campground: 'campsite',
  facility: 'sign_and_pedestrian',
  tour: 'compass',
  thingstodo: 'binoculars',
  site: 'monument',
  permit: 'ticket',
}

/**
 * Basemap (Protomaps) kind → icon + tier category. The icon drives the
 * `pois` layer's icon-image; the category drives its icon/text color and the
 * unresolved-tap minimal sheet's meta. This table is also the visibility
 * gate: only kinds listed here render as marks (landuse label points,
 * `tree`/`crossing` furniture, and other unmapped kinds stay hidden).
 */
export const BASEMAP_KINDS: Record<string, { icon: string; category: string }> = {
  fuel: { icon: 'fuel', category: 'automotive' },
  parking: { icon: 'parking', category: 'automotive' },
  hotel: { icon: 'lodging', category: 'lodging' },
  motel: { icon: 'lodging', category: 'lodging' },
  hostel: { icon: 'lodging', category: 'lodging' },
  guest_house: { icon: 'lodging', category: 'lodging' },
  restaurant: { icon: 'restaurant', category: 'food_drink' },
  cafe: { icon: 'cafe', category: 'food_drink' },
  fast_food: { icon: 'fast-food', category: 'food_drink' },
  bar: { icon: 'bar', category: 'food_drink' },
  pub: { icon: 'beer', category: 'food_drink' },
  brewery: { icon: 'beer', category: 'food_drink' },
  hospital: { icon: 'hospital', category: 'health' },
  clinic: { icon: 'doctor', category: 'health' },
  doctors: { icon: 'doctor', category: 'health' },
  pharmacy: { icon: 'pharmacy', category: 'health' },
  dentist: { icon: 'dentist', category: 'health' },
  supermarket: { icon: 'grocery', category: 'grocery' },
  convenience: { icon: 'convenience', category: 'grocery' },
  mall: { icon: 'shop', category: 'shopping' },
  marketplace: { icon: 'shop', category: 'shopping' },
  park: { icon: 'park', category: 'park' },
  peak: { icon: 'mountain', category: 'outdoors' },
  viewpoint: { icon: 'viewpoint', category: 'outdoors' },
  camp_site: { icon: 'campsite', category: 'camping' },
  picnic_site: { icon: 'picnic-site', category: 'outdoors' },
  beach: { icon: 'beach', category: 'outdoors' },
  information: { icon: 'information', category: 'outdoors' },
  library: { icon: 'library', category: 'civic' },
  museum: { icon: 'museum', category: 'attraction' },
  post_office: { icon: 'post', category: 'services' },
  townhall: { icon: 'town-hall', category: 'civic' },
  school: { icon: 'school', category: 'civic' },
  university: { icon: 'college', category: 'civic' },
  college: { icon: 'college', category: 'civic' },
  place_of_worship: { icon: 'place-of-worship', category: 'civic' },
  theatre: { icon: 'theatre', category: 'attraction' },
  bank: { icon: 'bank', category: 'services' },
}

/** Neutral icon for rows no table covers. */
export const FALLBACK_ICON = 'marker'

/** Sprite-qualified ref for GL layers (`icon-image` values). */
export function spriteRef(icon: string): string {
  return `poi:${icon}`
}

/** Sprite icon for a tier row: federal kind first, then its category. */
export function rowIcon(row: { source_kind: string; category: string | null }): string {
  return (
    FEDERAL_KIND_ICONS[row.source_kind] ??
    (row.category ? CATEGORY_ICONS[row.category] : undefined) ??
    FALLBACK_ICON
  )
}

// ── planetiler feature-id codec ──────────────────────────────────────────────

const OSM_TYPE_SHIFT = 2 ** 44
const TYPE_NAMES: Record<number, string> = { 1: 'node', 2: 'way', 3: 'relation' }
const TYPE_CODES: Record<string, number> = { node: 1, way: 2, relation: 3 }

/** Tile feature id → tier `source_id` ('node/123'), or null if unrecognized. */
export function decodeFeatureId(fid: number): string | null {
  const type = TYPE_NAMES[Math.floor(fid / OSM_TYPE_SHIFT)]
  return type ? `${type}/${fid % OSM_TYPE_SHIFT}` : null
}

/** Tier `source_id` → tile feature id, or null for non-OSM shapes. */
export function encodeFeatureId(sourceId: string): number | null {
  const [type, rest] = sourceId.split('/', 2)
  const code = TYPE_CODES[type]
  const osmId = Number(rest)
  return code && Number.isSafeInteger(osmId) ? code * OSM_TYPE_SHIFT + osmId : null
}
