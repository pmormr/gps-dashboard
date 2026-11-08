/**
 * Town-compass policy for the Drive view: which nearby settlements earn a
 * mark on the ring around the map, and where on the ring they sit. Pure —
 * Drive.svelte feeds it the `/api/places/nearby` candidates plus the live
 * heading. Every constant here is a road-tuning knob
 * (plans/drive-hud-plan.md D5–D7).
 *
 * The ring is the map rectangle's edge: a mark sits where the ray from the
 * van (the map centre) at the place's relative bearing meets the edge, so a
 * town passed on the left slides down the left side — no strips, no fold.
 *
 * Membership is four mechanisms, because one score can't satisfy "small
 * towns nearby, cities from far, cities don't dominate":
 *  1. a per-tier visibility radius (`TIER_RADII_KM`) — the server applies it,
 *     widened by `LEAVE_FACTOR` so current members have room to linger;
 *  2. metro shadowing — a place hidden by a bigger one in the same direction
 *     and distance band (Denver's suburb ring collapses onto Denver);
 *  3. slot quotas by tier, so a metro can't crowd out villages;
 *  4. hysteresis — the radius leave band and a sticky seat at the slot cutoff.
 */

import type { NearbyPlace } from './api'
import { angleDelta } from './live'

/** Visibility radius per population tier (index = tier − 1), km. */
export const TIER_RADII_KM: readonly number[] = [8, 35, 110, 250, 400]
/** A member stays visible out to `radius × LEAVE_FACTOR` (radius hysteresis). */
export const LEAVE_FACTOR = 1.15
/** Shadowing: hidden when a more-populous place sits within this bearing spread… */
export const SHADOW_DEG = 10
/** …and its distance is within this ratio band of the bigger place's. */
export const SHADOW_RATIO_LO = 0.6
export const SHADOW_RATIO_HI = 1.6
/** Ring seats reserved by tier — tiers 4–5 (100k+), tier 3 (10k–100k) — so a
 *  metro ring can't crowd out villages; tiers 0–2 take the rest of the ring
 *  cap, and any room still left (villages exhausted) fills nearest-first from
 *  whatever remains. The cap is a user setting (`drivePrefs.ts` ringCap);
 *  RING_CAP is its default. */
export const METRO_SLOTS = 3
export const TOWN_SLOTS = 5
export const RING_CAP = 14
/** Current members compete for slots at this fraction of their true distance. */
export const MEMBER_STICKINESS = 0.85
/** Refetch candidates after this much movement, or this much time. */
export const REFETCH_METERS = 2000
export const REFETCH_MS = 60_000
/** "In town" radius per tier (index = tier − 1), km: how far from a
 *  settlement's point you still count as *in* it (GeoNames has no polygons). */
export const TOWN_RADII_KM: readonly number[] = [1.5, 3, 7, 15, 25]
/** GNIS hamlets (tier 0 — no population; the towns GeoNames has no count
 *  for) are requested within HAMLET_KM, count as "in" within HAMLET_IN_KM,
 *  and earn ring marks within HAMLET_RING_KM at village priority (nearest
 *  first — a crossroads 1 km off outranks a village 5 km off, and shadowing
 *  still hides one sitting in a bigger place's direction). */
export const HAMLET_KM = 6
export const HAMLET_IN_KM = 2
export const HAMLET_RING_KM = 4

/** The settlement the van is in (or, failing that, nearest to) — the HUD readout. */
export interface Here {
  place: NearbyPlace
  /** Inside the place's in-town radius, vs merely the nearest candidate. */
  inside: boolean
}

/** A candidate's in-town radius, metres (tier 0 = GNIS hamlet). */
function inTownRadiusM(tier: number): number {
  if (tier < 1) return HAMLET_IN_KM * 1000
  return TOWN_RADII_KM[Math.min(Math.round(tier), TOWN_RADII_KM.length) - 1] * 1000
}

/**
 * The current town: of the populated candidates whose in-town radius
 * contains the van, the one the van is deepest inside (distance ÷ radius — a
 * city centre 5 km off beats a village 1.4 km off); the incumbent keeps its
 * seat out to LEAVE_FACTOR × radius so a boundary doesn't flicker. GNIS
 * hamlets (tier 0) are the fallback: only when no populated town contains
 * the van does the deepest-inside hamlet win (a city's neighbourhoods are
 * GNIS populated places too — the city must beat them). With nothing
 * containing the van, the nearest candidate of any kind, `inside: false`.
 */
export function currentTown(
  candidates: readonly NearbyPlace[],
  previousId: number | null,
): Here | null {
  const deepest = (pool: readonly NearbyPlace[]): NearbyPlace | null => {
    let best: NearbyPlace | null = null
    let bestRatio = Infinity
    for (const c of pool) {
      const ratio = c.distance_m / inTownRadiusM(c.tier)
      const limit = c.id === previousId ? LEAVE_FACTOR : 1
      if (ratio <= limit && ratio < bestRatio) {
        best = c
        bestRatio = ratio
      }
    }
    return best
  }
  const town = deepest(candidates.filter(isPopulated)) ?? deepest(candidates.filter((c) => !isPopulated(c)))
  if (town) return { place: town, inside: true }
  let nearest: NearbyPlace | null = null
  for (const c of candidates) {
    if (!nearest || c.distance_m < nearest.distance_m) nearest = c
  }
  return nearest ? { place: nearest, inside: false } : null
}

/** One settlement that earned a mark. */
export interface Mark {
  id: number
  name: string
  tier: number
  /** Resident count; null for a GNIS hamlet (tier 0). */
  population: number | null
  distanceM: number
  /** Bearing from the van, degrees true. */
  bearing: number
}

/** Membership carried between selections — the hysteresis memory. */
export interface CompassState {
  members: Set<number>
}

/** A fresh, empty membership. */
export function newCompassState(): CompassState {
  return { members: new Set() }
}

/** The radii to request from the server: enter radii widened by the leave band. */
export function requestRadiiKm(): number[] {
  return TIER_RADII_KM.map((r) => r * LEAVE_FACTOR)
}

/** The hamlet radius to request, widened by the leave band like the tiers. */
export function requestHamletsKm(): number {
  return HAMLET_KM * LEAVE_FACTOR
}

/** Whether a candidate carries a population (GeoNames) vs a GNIS hamlet. */
export function isPopulated(c: Pick<NearbyPlace, 'tier'>): boolean {
  return c.tier >= 1
}

/** Bearing relative to the heading (north-up before any heading exists). */
export function relBearing(heading: number | null, bearing: number): number {
  return angleDelta(heading ?? 0, bearing)
}

/** The tier's visibility radius in metres, widened for current members. */
function visibleRadiusM(tier: number, member: boolean): number {
  const km = tier < 1 ? HAMLET_RING_KM : TIER_RADII_KM[Math.min(tier, TIER_RADII_KM.length) - 1]
  return km * 1000 * (member ? LEAVE_FACTOR : 1)
}

/**
 * Drop every candidate shadowed by a more-populous one in the same direction
 * and distance band. Returns the survivors, most populous first.
 */
export function unshadowed(candidates: readonly NearbyPlace[]): NearbyPlace[] {
  const byPop = [...candidates].sort((a, b) => (b.population ?? 0) - (a.population ?? 0))
  const kept: NearbyPlace[] = []
  for (const c of byPop) {
    const hidden = kept.some((k) => {
      if (Math.abs(angleDelta(k.bearing_deg, c.bearing_deg)) > SHADOW_DEG) return false
      const ratio = c.distance_m / Math.max(k.distance_m, 1)
      return ratio >= SHADOW_RATIO_LO && ratio <= SHADOW_RATIO_HI
    })
    if (!hidden) kept.push(c)
  }
  return kept
}

/**
 * Select the current marks from the server's candidates, updating `state`.
 * `ringCap` bounds the total: the metro/town quotas are reserved seats (trimmed
 * to the cap), villages take the remaining room, and leftover room — a
 * town-dense area with few villages — fills nearest-first across the rest, so
 * raising the cap always shows more when more exist.
 */
export function selectMarks(
  candidates: readonly NearbyPlace[],
  state: CompassState,
  ringCap = RING_CAP,
): Mark[] {
  const visible = candidates.filter(
    (c) => c.distance_m <= visibleRadiusM(c.tier, state.members.has(c.id)),
  )
  const sticky = (c: NearbyPlace): number =>
    c.distance_m * (state.members.has(c.id) ? MEMBER_STICKINESS : 1)
  const ranked = unshadowed(visible).sort((a, b) => sticky(a) - sticky(b))
  const metros = ranked.filter((c) => c.tier >= 4).slice(0, Math.min(METRO_SLOTS, ringCap))
  const towns = ranked
    .filter((c) => c.tier === 3)
    .slice(0, Math.max(0, Math.min(TOWN_SLOTS, ringCap - metros.length)))
  const room = Math.max(0, ringCap - metros.length - towns.length)
  const villages = ranked.filter((c) => c.tier <= 2).slice(0, room)
  const seated = new Set([...metros, ...towns, ...villages].map((c) => c.id))
  const fill = ranked.filter((c) => !seated.has(c.id)).slice(0, ringCap - seated.size)
  const selected = [...metros, ...towns, ...villages, ...fill].map((c) => ({
    id: c.id,
    name: c.name,
    tier: c.tier,
    population: c.population,
    distanceM: c.distance_m,
    bearing: c.bearing_deg,
  }))
  state.members = new Set(selected.map((m) => m.id))
  return selected
}

export type Edge = 'top' | 'right' | 'bottom' | 'left'

/** The ring rectangle (the map area the ring runs around), px. */
export interface Frame {
  width: number
  height: number
}

/** A box in frame px — a placed label, or fixed chrome labels must avoid. */
export interface Rect {
  x: number
  y: number
  width: number
  height: number
}

/** Where a relative bearing's ray from the frame centre meets the frame edge. */
export function ringPoint(rel: number, frame: Frame): { x: number; y: number; edge: Edge } {
  const dx = Math.sin((rel * Math.PI) / 180)
  const dy = -Math.cos((rel * Math.PI) / 180)
  const cx = frame.width / 2
  const cy = frame.height / 2
  const tx = Math.abs(dx) < 1e-9 ? Infinity : cx / Math.abs(dx)
  const ty = Math.abs(dy) < 1e-9 ? Infinity : cy / Math.abs(dy)
  const px = (v: number): number => Math.round(v * 100) / 100
  if (tx < ty) {
    return { x: dx < 0 ? 0 : frame.width, y: px(cy + dy * tx), edge: dx < 0 ? 'left' : 'right' }
  }
  return { x: px(cx + dx * ty), y: dy < 0 ? 0 : frame.height, edge: dy < 0 ? 'top' : 'bottom' }
}

/** A label laid out on the ring: its box and the exact edge point it marks. */
export interface PlacedMark<T> {
  mark: T
  edge: Edge
  /** Label box, px from the frame's top-left. */
  x: number
  y: number
  width: number
  height: number
  /** The ring point (tick) for the mark's bearing. */
  tickX: number
  tickY: number
}

/** Default label distance in from the frame edge (px) — along the edge *and*
 *  at the corner clamp; a user setting (`drivePrefs.ts` labelInset). */
export const LABEL_INSET_PX = 12
/** Second-row inward push on the long edges and on the sides, px (≥ label height + gap). */
export const ROW_PUSH_TOPBOTTOM_PX = 32
export const ROW_PUSH_SIDES_PX = 100

/**
 * Greedy 2-D label placement around the ring: marks in priority order
 * (bigger tier, then nearer) each take their edge spot, or the spot pushed
 * one row inward, if neither overlaps an earlier label; otherwise dropped.
 * Top/bottom labels centre on the tick (kept inside the frame), side labels
 * hug the edge beside the tick.
 */
export function placeMarks<T extends Mark>(
  marks: readonly T[],
  heading: number | null,
  frame: Frame,
  size: (mark: T) => { width: number; height: number },
  inset = LABEL_INSET_PX,
  gapPx = 4,
  obstacles: readonly Rect[] = [],
): PlacedMark<T>[] {
  const ordered = [...marks].sort((a, b) => b.tier - a.tier || a.distanceM - b.distanceM)
  const placed: PlacedMark<T>[] = []
  // Fixed chrome over the ring (the mini-map) takes part in the clash test
  // like an already-placed label, so a mark under it moves inward or drops.
  const taken: Rect[] = [...obstacles]
  const clamp = (v: number, lo: number, hi: number): number => Math.min(Math.max(v, lo), hi)
  for (const mark of ordered) {
    const { width, height } = size(mark)
    const p = ringPoint(relBearing(heading, mark.bearing), frame)
    for (let row = 0; row < 2; row++) {
      let x: number
      let y: number
      if (p.edge === 'top' || p.edge === 'bottom') {
        x = clamp(p.x - width / 2, inset, Math.max(inset, frame.width - width - inset))
        const push = inset + row * ROW_PUSH_TOPBOTTOM_PX
        y = p.edge === 'top' ? push : frame.height - push - height
      } else {
        y = clamp(p.y - height / 2, inset, Math.max(inset, frame.height - height - inset))
        const push = inset + row * ROW_PUSH_SIDES_PX
        x = p.edge === 'left' ? push : frame.width - push - width
      }
      const clash = taken.some(
        (q) =>
          x < q.x + q.width + gapPx &&
          q.x < x + width + gapPx &&
          y < q.y + q.height + gapPx &&
          q.y < y + height + gapPx,
      )
      if (!clash) {
        placed.push({ mark, edge: p.edge, x, y, width, height, tickX: p.x, tickY: p.y })
        taken.push({ x, y, width, height })
        break
      }
    }
  }
  return placed
}

/** Mark scale at the far edge of its tier radius, and right next to the van. */
export const PROXIMITY_SCALE_FAR = 0.85
export const PROXIMITY_SCALE_NEAR = 1.25

/**
 * How deep a mark sits inside its *own tier's* radius, 0 (at the edge) … 1
 * (on top of the van). Normalised per tier so a village 2 km into its 8 km
 * radius reads "right here" while a metro keeps a mid size from afar —
 * raw distance would make cities permanently tiny.
 */
export function proximity(mark: Pick<Mark, 'tier' | 'distanceM'>): number {
  const r = visibleRadiusM(mark.tier, false)
  const p = 1 - mark.distanceM / r
  return p < 0 ? 0 : p > 1 ? 1 : p
}

/** Label/tick scale for a mark's proximity (linear between the two constants). */
export function proximityScale(mark: Pick<Mark, 'tier' | 'distanceM'>): number {
  return PROXIMITY_SCALE_FAR + (PROXIMITY_SCALE_NEAR - PROXIMITY_SCALE_FAR) * proximity(mark)
}

// ── Label typography ───────────────────────────────────────────────────────
// drive.css reads these as custom properties set on the ring root, so the
// placement estimate below and the painted size derive from one place.
/** Name font size (px); cities (tier ≥ CITY_TIER) use CITY_NAME_PX. */
export const NAME_PX = 16
export const CITY_NAME_PX = 18
/** The population + miles line (px). */
export const META_PX = 12
/** First tier painted as a city (bigger, 800 weight). */
export const CITY_TIER = 4
/** Mean glyph advance as a fraction of font size per tier — system-ui at the
 *  weights drive.css uses (600 / 600 / 700 / 800 / 800). */
const NAME_EM: readonly number[] = [0.555, 0.555, 0.585, 0.63, 0.63]
/** The widest meta line ("1.2M · 120 mi") in ems of META_PX. */
const META_EM = 7.6
/** Ring-dot diameter (px, before proximity scale) per tier: the at-a-glance
 *  population cue — village / town / city — readable without reading. */
const TICK_PX: readonly number[] = [6, 6, 9, 13, 13]
const HAMLET_TICK_PX = 5

/** The ring dot's diameter for a tier, px (before the proximity scale); hamlets smallest. */
export function tickPx(tier: number): number {
  if (tier < 1) return HAMLET_TICK_PX
  return TICK_PX[Math.min(Math.round(tier), TICK_PX.length) - 1]
}
const LINE_GAP_PX = 1

/** Whether a tier paints as a city. */
export function isCity(tier: number): boolean {
  return tier >= CITY_TIER
}

/**
 * Label box estimate for placement — the wider of the two stacked lines (name
 * over population + miles), scaled with proximity — avoids DOM measurement per frame.
 */
export function labelSize(mark: Pick<Mark, 'tier' | 'distanceM' | 'name'>): {
  width: number
  height: number
} {
  const s = proximityScale(mark)
  const tier = Math.min(Math.max(Math.round(mark.tier), 1), NAME_EM.length)
  const namePx = isCity(tier) ? CITY_NAME_PX : NAME_PX
  const nameW = mark.name.length * namePx * NAME_EM[tier - 1]
  return {
    width: (Math.max(nameW, META_EM * META_PX) + 4) * s,
    height: (namePx * 1.05 + LINE_GAP_PX + META_PX + 2) * s,
  }
}

// US states + DC and Canadian provinces by the importer's admin1 *name*
// (GeoNames maps the code to its name at import, so the postal code is
// rebuilt here). Anything unmapped shows its full region name.
const REGION_ABBREV: Readonly<Record<string, string>> = {
  Alabama: 'AL', Alaska: 'AK', Arizona: 'AZ', Arkansas: 'AR', California: 'CA',
  Colorado: 'CO', Connecticut: 'CT', Delaware: 'DE', 'District of Columbia': 'DC',
  Florida: 'FL', Georgia: 'GA', Hawaii: 'HI', Idaho: 'ID', Illinois: 'IL',
  Indiana: 'IN', Iowa: 'IA', Kansas: 'KS', Kentucky: 'KY', Louisiana: 'LA',
  Maine: 'ME', Maryland: 'MD', Massachusetts: 'MA', Michigan: 'MI', Minnesota: 'MN',
  Mississippi: 'MS', Missouri: 'MO', Montana: 'MT', Nebraska: 'NE', Nevada: 'NV',
  'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM', 'New York': 'NY',
  'North Carolina': 'NC', 'North Dakota': 'ND', Ohio: 'OH', Oklahoma: 'OK',
  Oregon: 'OR', Pennsylvania: 'PA', 'Rhode Island': 'RI', 'South Carolina': 'SC',
  'South Dakota': 'SD', Tennessee: 'TN', Texas: 'TX', Utah: 'UT', Vermont: 'VT',
  Virginia: 'VA', Washington: 'WA', 'West Virginia': 'WV', Wisconsin: 'WI',
  Wyoming: 'WY', 'Puerto Rico': 'PR',
  Alberta: 'AB', 'British Columbia': 'BC', Manitoba: 'MB', 'New Brunswick': 'NB',
  'Newfoundland and Labrador': 'NL', 'Nova Scotia': 'NS', 'Northwest Territories': 'NT',
  Nunavut: 'NU', Ontario: 'ON', 'Prince Edward Island': 'PE', Quebec: 'QC',
  Saskatchewan: 'SK', Yukon: 'YT',
}

/** "PA" for Pennsylvania, "ON" for Ontario; an unmapped region as given; '' for none. */
export function regionAbbrev(region: string | null | undefined): string {
  if (!region) return ''
  return REGION_ABBREV[region] ?? region
}

/** "Philadelphia, PA" — the name with its region, or the bare name without one. */
export function placeLabel(place: Pick<NearbyPlace, 'name' | 'region'>): string {
  const r = regionAbbrev(place.region)
  return r ? `${place.name}, ${r}` : place.name
}

/** Compact population for a ring label: 850 · 4.2k · 715k · 2.7M. */
export function fmtPopulation(n: number): string {
  if (n < 1000) return String(Math.max(0, Math.round(n)))
  if (n < 10_000) return `${(n / 1000).toFixed(1)}k`
  if (n < 1_000_000) return `${Math.round(n / 1000)}k`
  return `${(n / 1_000_000).toFixed(1)}M`
}

/** Compact miles for a ring label: one decimal under 10 mi, whole above. */
export function fmtMiles(meters: number): string {
  const miles = meters / 1609.344
  return miles < 10 ? `${miles.toFixed(1)} mi` : `${Math.round(miles)} mi`
}
