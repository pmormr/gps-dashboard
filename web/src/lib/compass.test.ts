import { describe, expect, it } from 'vitest'

import type { NearbyPlace } from './api'
import {
  assignStrip,
  fmtMiles,
  FOLD_HYSTERESIS_DEG,
  LEAVE_FACTOR,
  METRO_SLOTS,
  newCompassState,
  packLabels,
  pips,
  requestRadiiKm,
  selectMarks,
  stripX,
  TIER_RADII_KM,
  TOWN_SLOTS,
  unshadowed,
} from './compass'

let nextId = 1
function place(over: Partial<NearbyPlace> & { population: number; km: number; bearing: number }): NearbyPlace {
  const tier = Math.min(5, Math.max(1, Math.floor(Math.log10(over.population)) - 1))
  return {
    id: over.id ?? nextId++,
    name: over.name ?? `P${nextId}`,
    summary: null,
    lat: 0,
    lon: 0,
    population: over.population,
    tier,
    distance_m: over.km * 1000,
    bearing_deg: over.bearing,
  }
}

describe('requestRadiiKm', () => {
  it('widens every tier radius by the leave factor', () => {
    expect(requestRadiiKm()).toEqual(TIER_RADII_KM.map((r) => r * LEAVE_FACTOR))
  })
})

describe('stripX', () => {
  it('unrolls the ring: ahead left→right, behind continues around the far side', () => {
    expect(stripX('ahead', -90)).toBe(0)
    expect(stripX('ahead', 0)).toBe(0.5)
    expect(stripX('ahead', 90)).toBe(1)
    expect(stripX('behind', -90)).toBe(0)
    expect(stripX('behind', -180)).toBe(0.5)
    expect(stripX('behind', 180)).toBe(0.5)
    expect(stripX('behind', 90)).toBe(1)
  })

  it('clamps marks lingering in the fold dead zone to the edge', () => {
    expect(stripX('ahead', 95)).toBe(1)
    expect(stripX('behind', 85)).toBe(1)
    expect(stripX('behind', -85)).toBe(0)
  })
})

describe('assignStrip', () => {
  it('splits at ±90 for newcomers', () => {
    expect(assignStrip(89, undefined)).toBe('ahead')
    expect(assignStrip(91, undefined)).toBe('behind')
    expect(assignStrip(-91, undefined)).toBe('behind')
  })

  it('holds a member on its strip through the dead zone', () => {
    expect(assignStrip(90 + FOLD_HYSTERESIS_DEG, 'ahead')).toBe('ahead')
    expect(assignStrip(90 + FOLD_HYSTERESIS_DEG + 1, 'ahead')).toBe('behind')
    expect(assignStrip(90 - FOLD_HYSTERESIS_DEG, 'behind')).toBe('behind')
    expect(assignStrip(90 - FOLD_HYSTERESIS_DEG - 1, 'behind')).toBe('ahead')
  })
})

describe('unshadowed', () => {
  it('collapses a suburb ring onto its metro but keeps a distinct direction', () => {
    const denver = place({ name: 'Denver', population: 729_019, km: 124.5, bearing: 63.7 })
    const lakewood = place({ name: 'Lakewood', population: 152_597, km: 115.4, bearing: 63.6 })
    const golden = place({ name: 'Golden', population: 20_330, km: 107.6, bearing: 58.2 })
    const centennial = place({ name: 'Centennial', population: 109_741, km: 126.8, bearing: 72.8 })
    const boulder = place({ name: 'Boulder', population: 106_803, km: 121.9, bearing: 45.4 })
    const kept = unshadowed([golden, lakewood, boulder, centennial, denver])
    expect(kept.map((p) => p.name)).toEqual(['Denver', 'Boulder'])
  })

  it('does not shadow a near village under a far city on the same bearing', () => {
    const city = place({ name: 'City', population: 500_000, km: 120, bearing: 10 })
    const village = place({ name: 'Village', population: 900, km: 5, bearing: 11 })
    expect(unshadowed([city, village]).map((p) => p.name)).toEqual(['City', 'Village'])
  })
})

describe('selectMarks', () => {
  it('applies tier radii on entry and the leave band for members', () => {
    const state = newCompassState()
    const village = place({ id: 1, population: 800, km: 7.9, bearing: 0 })
    expect(selectMarks([village], state, 0).map((m) => m.id)).toEqual([1])
    const drifting = { ...village, distance_m: 8_000 * LEAVE_FACTOR - 1 }
    expect(selectMarks([drifting], state, 0).map((m) => m.id)).toEqual([1])
    const gone = { ...village, distance_m: 8_000 * LEAVE_FACTOR + 1 }
    expect(selectMarks([gone], state, 0)).toEqual([])
    // A newcomer at the same distance does not get the leave band.
    expect(selectMarks([{ ...drifting, id: 2 }], newCompassState(), 0)).toEqual([])
  })

  it('caps metros and towns per strip so villages keep their seats', () => {
    const cands: NearbyPlace[] = []
    for (let i = 0; i < 4; i++) cands.push(place({ population: 300_000, km: 100 + i, bearing: -80 + i * 40 }))
    for (let i = 0; i < 5; i++) cands.push(place({ population: 30_000, km: 50 + i, bearing: -70 + i * 30 }))
    for (let i = 0; i < 6; i++) cands.push(place({ population: 2_000, km: 10 + i, bearing: -60 + i * 20 }))
    const marks = selectMarks(cands, newCompassState(), 0)
    const ahead = marks.filter((m) => m.strip === 'ahead')
    expect(ahead.filter((m) => m.tier >= 4)).toHaveLength(METRO_SLOTS)
    expect(ahead.filter((m) => m.tier === 3)).toHaveLength(TOWN_SLOTS)
    expect(ahead.filter((m) => m.tier <= 2)).toHaveLength(3)
    expect(ahead).toHaveLength(8)
  })

  it('places marks relative to the heading and remembers their strip', () => {
    const state = newCompassState()
    const east = place({ id: 7, population: 5_000, km: 20, bearing: 90 })
    const [mark] = selectMarks([east], state, 90)
    expect(mark.rel).toBe(0)
    expect(mark.strip).toBe('ahead')
    expect(mark.x).toBe(0.5)
    expect(state.strips.get(7)).toBe('ahead')
    // Turn so the town sits 95° off the nose: a member holds its strip.
    const [held] = selectMarks([east], state, 355)
    expect(held.rel).toBe(95)
    expect(held.strip).toBe('ahead')
    expect(held.x).toBe(1)
  })

  it('prefers a current member at the slot cutoff (sticky distance)', () => {
    const state = newCompassState()
    const members = Array.from({ length: 8 }, (_, i) =>
      place({ id: 100 + i, population: 2_000, km: 10 + i, bearing: -80 + i * 20 }),
    )
    selectMarks(members, state, 0)
    const newcomer = place({ id: 999, population: 2_000, km: 16.5, bearing: 85 })
    const ids = selectMarks([...members, newcomer], state, 0).map((m) => m.id)
    expect(ids).not.toContain(999)
    expect(ids).toContain(107) // the 17 km member keeps its seat at 17 × 0.85
  })
})

describe('packLabels', () => {
  const measure = () => 100
  it('puts overlapping labels on the next row, in tier-then-distance priority', () => {
    const marks = [
      { name: 'a', x: 0.5, tier: 2, distanceM: 5_000 },
      { name: 'b', x: 0.52, tier: 4, distanceM: 50_000 },
      { name: 'c', x: 0.1, tier: 1, distanceM: 2_000 },
    ]
    const placed = packLabels(marks, 1000, measure)
    const byName = Object.fromEntries(placed.map((p) => [p.mark.name, p]))
    expect(byName.b.row).toBe(0)
    expect(byName.a.row).toBe(1)
    expect(byName.c.row).toBe(0)
    expect(byName.c.left).toBe(50)
  })

  it('keeps labels inside the strip and drops what no row can hold', () => {
    const marks = [
      { name: 'edge', x: 1, tier: 3, distanceM: 1 },
      { name: 'two', x: 0.99, tier: 2, distanceM: 1 },
      { name: 'three', x: 0.98, tier: 1, distanceM: 1 },
    ]
    const placed = packLabels(marks, 400, measure)
    expect(placed.map((p) => p.mark.name)).toEqual(['edge', 'two'])
    expect(placed[0].left).toBe(300)
  })
})

describe('pips + fmtMiles', () => {
  it('renders one filled cell per tier', () => {
    expect(pips(1)).toBe('▮▯▯▯▯')
    expect(pips(3)).toBe('▮▮▮▯▯')
    expect(pips(5)).toBe('▮▮▮▮▮')
    expect(pips(9)).toBe('▮▮▮▮▮')
  })

  it('formats compact miles', () => {
    expect(fmtMiles(1609.344)).toBe('1.0 mi')
    expect(fmtMiles(16_200)).toBe('10 mi')
    expect(fmtMiles(200_000)).toBe('124 mi')
  })
})
