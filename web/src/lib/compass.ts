/**
 * Town-compass policy for the Drive view: which nearby settlements earn a
 * mark on the ahead/behind bearing strips, and where on the strip they sit.
 * Pure — Drive.svelte feeds it the `/api/places/nearby` candidates plus the
 * live heading. Every constant here is a road-tuning knob
 * (plans/drive-hud-plan.md D5–D7).
 *
 * Four mechanisms, because one score can't satisfy "small towns nearby,
 * cities from far, cities don't dominate":
 *  1. a per-tier visibility radius (`TIER_RADII_KM`) — the server applies it,
 *     widened by `LEAVE_FACTOR` so current members have room to linger;
 *  2. metro shadowing — a place hidden by a bigger one in the same direction
 *     and distance band (Denver's suburb ring collapses onto Denver);
 *  3. per-strip slot quotas by tier, so a metro can't crowd out villages;
 *  4. hysteresis — radius leave band, sticky membership at the slot cutoff,
 *     and a dead zone at the ±90° fold between the two strips.
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
/** Per-strip slots: tiers 4–5 (100k+), tier 3 (10k–100k); tiers 1–2 fill the rest. */
export const METRO_SLOTS = 2
export const TOWN_SLOTS = 3
export const STRIP_CAP = 8
/** A mark keeps its strip until this far past the ±90° fold line. */
export const FOLD_HYSTERESIS_DEG = 10
/** Current members compete for slots at this fraction of their true distance. */
export const MEMBER_STICKINESS = 0.85
/** Refetch candidates after this much movement, or this much time. */
export const REFETCH_METERS = 2000
export const REFETCH_MS = 60_000
/** Pip-bar cells (one per tier). */
export const PIP_CELLS = TIER_RADII_KM.length

export type Strip = 'ahead' | 'behind'

/** One settlement placed on a strip. */
export interface Mark {
  id: number
  name: string
  tier: number
  population: number
  distanceM: number
  /** Bearing from the van, degrees true. */
  bearing: number
  /** Bearing relative to the heading, (−180, 180]. */
  rel: number
  strip: Strip
  /** Position along the strip, 0 (left edge) … 1 (right edge). */
  x: number
}

/** Membership carried between selections — the hysteresis memory. */
export interface CompassState {
  strips: Map<number, Strip>
}

/** A fresh, empty membership. */
export function newCompassState(): CompassState {
  return { strips: new Map() }
}

/** The radii to request from the server: enter radii widened by the leave band. */
export function requestRadiiKm(): number[] {
  return TIER_RADII_KM.map((r) => r * LEAVE_FACTOR)
}

/** Bearing relative to the heading (north-up before any heading exists). */
export function relBearing(heading: number | null, bearing: number): number {
  return angleDelta(heading ?? 0, bearing)
}

/**
 * Strip position for a relative bearing. Ahead: −90 → 0, 0 → ½, +90 → 1.
 * Behind is the ring unrolled the other way: −90 → 0, ±180 → ½, +90 → 1, so
 * a place sliding off the top-left edge reappears at the bottom-left edge.
 * Clamped, so a mark lingering in the fold dead zone pins to the edge.
 */
export function stripX(strip: Strip, rel: number): number {
  const x = strip === 'ahead' ? (rel + 90) / 180 : rel < 0 ? (-90 - rel) / 180 : 0.5 + (180 - rel) / 180
  return x < 0 ? 0 : x > 1 ? 1 : x
}

/** Which strip a relative bearing belongs on, with the fold dead zone for members. */
export function assignStrip(rel: number, previous: Strip | undefined): Strip {
  const a = Math.abs(rel)
  if (previous === 'ahead') return a <= 90 + FOLD_HYSTERESIS_DEG ? 'ahead' : 'behind'
  if (previous === 'behind') return a >= 90 - FOLD_HYSTERESIS_DEG ? 'behind' : 'ahead'
  return a <= 90 ? 'ahead' : 'behind'
}

/** The tier's visibility radius in metres, widened for current members. */
function visibleRadiusM(tier: number, member: boolean): number {
  const idx = Math.min(Math.max(tier, 1), TIER_RADII_KM.length) - 1
  return TIER_RADII_KM[idx] * 1000 * (member ? LEAVE_FACTOR : 1)
}

/**
 * Drop every candidate shadowed by a more-populous one in the same direction
 * and distance band. Returns the survivors, most populous first.
 */
export function unshadowed(candidates: readonly NearbyPlace[]): NearbyPlace[] {
  const byPop = [...candidates].sort((a, b) => b.population - a.population)
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
 * Select this frame's marks from the server's candidates, updating `state`.
 * `heading` is the smoothed live heading (null before any reading).
 */
export function selectMarks(
  candidates: readonly NearbyPlace[],
  state: CompassState,
  heading: number | null,
): Mark[] {
  const visible = candidates.filter(
    (c) => c.distance_m <= visibleRadiusM(c.tier, state.strips.has(c.id)),
  )
  const marks: Mark[] = unshadowed(visible).map((c) => {
    const rel = relBearing(heading, c.bearing_deg)
    const strip = assignStrip(rel, state.strips.get(c.id))
    return {
      id: c.id,
      name: c.name,
      tier: c.tier,
      population: c.population,
      distanceM: c.distance_m,
      bearing: c.bearing_deg,
      rel,
      strip,
      x: stripX(strip, rel),
    }
  })

  const selected: Mark[] = []
  for (const strip of ['ahead', 'behind'] as const) {
    const onStrip = marks
      .filter((m) => m.strip === strip)
      .sort((a, b) => stickyDistance(a, state) - stickyDistance(b, state))
    const metros = onStrip.filter((m) => m.tier >= 4).slice(0, METRO_SLOTS)
    const towns = onStrip.filter((m) => m.tier === 3).slice(0, TOWN_SLOTS)
    const room = Math.max(0, STRIP_CAP - metros.length - towns.length)
    const villages = onStrip.filter((m) => m.tier <= 2).slice(0, room)
    selected.push(...metros, ...towns, ...villages)
  }

  state.strips = new Map(selected.map((m) => [m.id, m.strip]))
  return selected.sort((a, b) => a.x - b.x)
}

function stickyDistance(mark: Mark, state: CompassState): number {
  return mark.distanceM * (state.strips.has(mark.id) ? MEMBER_STICKINESS : 1)
}

/** A label laid out on a strip: its row and left edge in px. */
export interface PlacedLabel<T> {
  mark: T
  row: number
  left: number
  width: number
}

/**
 * 1-D greedy label packing: marks in priority order (bigger tier, then
 * nearer) each take the first row where they don't overlap an earlier
 * label; past `rows` rows they're dropped. Labels are centred on their
 * strip position and kept inside the strip.
 */
export function packLabels<T extends { x: number; tier: number; distanceM: number }>(
  marks: readonly T[],
  widthPx: number,
  measure: (mark: T) => number,
  rows = 2,
  gapPx = 6,
): PlacedLabel<T>[] {
  const ordered = [...marks].sort((a, b) => b.tier - a.tier || a.distanceM - b.distanceM)
  const placed: PlacedLabel<T>[] = []
  for (const mark of ordered) {
    const width = Math.min(measure(mark), widthPx)
    const left = Math.min(Math.max(mark.x * widthPx - width / 2, 0), widthPx - width)
    for (let row = 0; row < rows; row++) {
      const clash = placed.some(
        (p) => p.row === row && left < p.left + p.width + gapPx && p.left < left + width + gapPx,
      )
      if (!clash) {
        placed.push({ mark, row, left, width })
        break
      }
    }
  }
  return placed
}

/** The population pip bar: one filled cell per tier (`▮▮▯▯▯` = 1k–10k). */
export function pips(tier: number): string {
  const filled = Math.min(Math.max(Math.round(tier), 0), PIP_CELLS)
  return '▮'.repeat(filled) + '▯'.repeat(PIP_CELLS - filled)
}

/** Compact miles for a strip label: one decimal under 10 mi, whole above. */
export function fmtMiles(meters: number): string {
  const miles = meters / 1609.344
  return miles < 10 ? `${miles.toFixed(1)} mi` : `${Math.round(miles)} mi`
}
