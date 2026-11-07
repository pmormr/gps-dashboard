import { describe, expect, it } from 'vitest'

import type { NearbyPlace } from './api'
import {
  fmtMiles,
  LEAVE_FACTOR,
  type Mark,
  METRO_SLOTS,
  newCompassState,
  pips,
  placeMarks,
  requestRadiiKm,
  ringPoint,
  selectMarks,
  TIER_RADII_KM,
  TOWN_SLOTS,
  unshadowed,
} from './compass'

let nextId = 1
function place(
  over: Partial<NearbyPlace> & { population: number; km: number; bearing: number },
): NearbyPlace {
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

function mark(over: Partial<Mark> & { bearing: number }): Mark {
  return { id: over.id ?? nextId++, name: 'm', tier: 2, population: 2000, distanceM: 5000, ...over }
}

describe('requestRadiiKm', () => {
  it('widens every tier radius by the leave factor', () => {
    expect(requestRadiiKm()).toEqual(TIER_RADII_KM.map((r) => r * LEAVE_FACTOR))
  })
})

describe('ringPoint', () => {
  const frame = { width: 400, height: 600 }
  it('hits the four edge midpoints at the cardinal relative bearings', () => {
    expect(ringPoint(0, frame)).toEqual({ x: 200, y: 0, edge: 'top' })
    expect(ringPoint(90, frame)).toEqual({ x: 400, y: 300, edge: 'right' })
    expect(ringPoint(180, frame)).toEqual({ x: 200, y: 600, edge: 'bottom' })
    expect(ringPoint(-90, frame)).toEqual({ x: 0, y: 300, edge: 'left' })
  })

  it('reaches the corners through the diagonal and picks the side past it', () => {
    const corner = ringPoint(Math.atan2(200, 300) * (180 / Math.PI), frame)
    expect(corner.x).toBeCloseTo(400, 1)
    expect(corner.y).toBeCloseTo(0, 1)
    const side = ringPoint(45, frame)
    expect(side.edge).toBe('right')
    expect(side.y).toBeCloseTo(100, 1)
    const top = ringPoint(20, frame)
    expect(top.edge).toBe('top')
    expect(top.x).toBeCloseTo(200 + 300 * Math.tan((20 * Math.PI) / 180), 1)
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
    expect(selectMarks([village], state).map((m) => m.id)).toEqual([1])
    const drifting = { ...village, distance_m: 8_000 * LEAVE_FACTOR - 1 }
    expect(selectMarks([drifting], state).map((m) => m.id)).toEqual([1])
    const gone = { ...village, distance_m: 8_000 * LEAVE_FACTOR + 1 }
    expect(selectMarks([gone], state)).toEqual([])
    // A newcomer at the same distance does not get the leave band.
    expect(selectMarks([{ ...drifting, id: 2 }], newCompassState())).toEqual([])
  })

  it('caps metros and towns so villages keep their seats', () => {
    const cands: NearbyPlace[] = []
    for (let i = 0; i < 5; i++) cands.push(place({ population: 300_000, km: 100 + i, bearing: i * 60 }))
    for (let i = 0; i < 7; i++) cands.push(place({ population: 30_000, km: 50 + i, bearing: i * 45 }))
    for (let i = 0; i < 9; i++) cands.push(place({ population: 2_000, km: 10 + i, bearing: i * 30 }))
    const marks = selectMarks(cands, newCompassState())
    expect(marks.filter((m) => m.tier >= 4)).toHaveLength(METRO_SLOTS)
    expect(marks.filter((m) => m.tier === 3)).toHaveLength(TOWN_SLOTS)
    expect(marks.filter((m) => m.tier <= 2)).toHaveLength(6)
    expect(marks).toHaveLength(14)
  })

  it('prefers a current member at the slot cutoff (sticky distance)', () => {
    const state = newCompassState()
    const members = Array.from({ length: 14 }, (_, i) =>
      place({ id: 100 + i, population: 2_000, km: 10 + i, bearing: i * 25 }),
    )
    selectMarks(members, state)
    const newcomer = place({ id: 999, population: 2_000, km: 22.5, bearing: 12 })
    const ids = selectMarks([...members, newcomer], state).map((m) => m.id)
    expect(ids).not.toContain(999)
    expect(ids).toContain(113) // the 23 km member keeps its seat at 23 × 0.85
  })
})

describe('placeMarks', () => {
  const frame = { width: 400, height: 600 }
  const size = () => ({ width: 100, height: 26 })

  it('anchors labels by edge: centred on the tick top/bottom, hugging the sides', () => {
    const placed = placeMarks(
      [mark({ id: 1, bearing: 0 }), mark({ id: 2, bearing: 90 }), mark({ id: 3, bearing: 180 }), mark({ id: 4, bearing: 270 })],
      0,
      frame,
      size,
    )
    const by = Object.fromEntries(placed.map((p) => [p.mark.id, p]))
    expect(by[1]).toMatchObject({ edge: 'top', x: 150, y: 4, tickX: 200, tickY: 0 })
    expect(by[3]).toMatchObject({ edge: 'bottom', x: 150, y: 600 - 4 - 26 })
    expect(by[2]).toMatchObject({ edge: 'right', x: 400 - 4 - 100, y: 287 })
    expect(by[4]).toMatchObject({ edge: 'left', x: 4, y: 287 })
  })

  it('positions relative to the heading', () => {
    const [p] = placeMarks([mark({ bearing: 90 })], 90, frame, size)
    expect(p.edge).toBe('top')
  })

  it('pushes a colliding label one row inward, then drops', () => {
    const marks = [
      mark({ id: 1, tier: 4, bearing: 0 }),
      mark({ id: 2, tier: 2, bearing: 2 }),
      mark({ id: 3, tier: 1, bearing: -2 }),
    ]
    const placed = placeMarks(marks, 0, frame, size)
    expect(placed.map((p) => [p.mark.id, p.y])).toEqual([
      [1, 4],
      [2, 36],
    ])
  })

  it('keeps a corner label inside the frame while the tick stays on the edge', () => {
    const [p] = placeMarks([mark({ bearing: 30 })], 0, frame, size)
    expect(p.edge).toBe('top')
    expect(p.x).toBe(300)
    expect(p.tickX).toBeCloseTo(200 + 300 * Math.tan(Math.PI / 6), 1)
  })
})

describe('proximity', () => {
  it('normalises by the tier radius so a near village outranks a mid-radius metro', async () => {
    const { proximity, proximityScale, PROXIMITY_SCALE_FAR, PROXIMITY_SCALE_NEAR } =
      await import('./compass')
    expect(proximity({ tier: 1, distanceM: 2_000 })).toBeCloseTo(0.75)
    expect(proximity({ tier: 4, distanceM: 125_000 })).toBeCloseTo(0.5)
    expect(proximity({ tier: 2, distanceM: 40_000 })).toBe(0) // past its radius (leave band)
    expect(proximityScale({ tier: 1, distanceM: 0 })).toBe(PROXIMITY_SCALE_NEAR)
    expect(proximityScale({ tier: 1, distanceM: 8_000 })).toBe(PROXIMITY_SCALE_FAR)
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
