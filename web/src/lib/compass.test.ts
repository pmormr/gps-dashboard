import { describe, expect, it } from 'vitest'

import type { NearbyPlace } from './api'
import {
  CITY_NAME_PX,
  currentTown,
  placeLabel,
  selectMarks as selectMarksFn,
  regionAbbrev,
  fmtMiles,
  labelSize,
  LABEL_INSET_PX,
  LEAVE_FACTOR,
  type Mark,
  METRO_SLOTS,
  newCompassState,
  fmtPopulation,
  tickPx,
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
    region: over.region ?? null,
    country: over.country ?? null,
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

  it('lets the ring cap widen or shrink the village room, quotas fixed', () => {
    const cands: NearbyPlace[] = []
    for (let i = 0; i < 5; i++) cands.push(place({ population: 300_000, km: 100 + i, bearing: i * 60 }))
    for (let i = 0; i < 7; i++) cands.push(place({ population: 30_000, km: 50 + i, bearing: i * 45 }))
    for (let i = 0; i < 20; i++) cands.push(place({ population: 2_000, km: 10 + i, bearing: i * 17 }))
    const wide = selectMarks(cands, newCompassState(), 24)
    expect(wide.filter((m) => m.tier >= 4)).toHaveLength(METRO_SLOTS)
    expect(wide.filter((m) => m.tier === 3)).toHaveLength(TOWN_SLOTS)
    expect(wide).toHaveLength(24)
    const tight = selectMarks(cands, newCompassState(), 6)
    expect(tight.filter((m) => m.tier <= 2)).toHaveLength(0)
    expect(tight.filter((m) => m.tier >= 4)).toHaveLength(METRO_SLOTS)
    expect(tight).toHaveLength(6)
  })

  it('fills leftover room past the quotas when villages run out', () => {
    // Town-dense flatland: 10 towns, 5 metros, 2 villages — the Quincy, FL
    // read that left the cap slider inert (2026-11-08).
    const cands: NearbyPlace[] = []
    for (let i = 0; i < 5; i++) cands.push(place({ population: 300_000, km: 100 + i, bearing: i * 60 }))
    for (let i = 0; i < 10; i++) cands.push(place({ population: 30_000, km: 50 + i, bearing: i * 33 }))
    for (let i = 0; i < 2; i++) cands.push(place({ population: 2_000, km: 10 + i, bearing: i * 90 }))
    expect(selectMarks(cands, newCompassState(), 14)).toHaveLength(14)
    const wide = selectMarks(cands, newCompassState(), 30)
    expect(wide).toHaveLength(17)
    // Villages were seated before any fill, and fill went nearest-first (towns before metros).
    expect(wide.filter((m) => m.tier <= 2)).toHaveLength(2)
    expect(wide.filter((m) => m.tier === 3)).toHaveLength(10)
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
      4,
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
    const placed = placeMarks(marks, 0, frame, size, 4)
    expect(placed.map((p) => [p.mark.id, p.y])).toEqual([
      [1, 4],
      [2, 36],
    ])
  })

  it('keeps a corner label inside the frame while the tick stays on the edge', () => {
    const [p] = placeMarks([mark({ bearing: 30 })], 0, frame, size, 4)
    expect(p.edge).toBe('top')
    expect(p.x).toBe(300 - 4)
    expect(p.tickX).toBeCloseTo(200 + 300 * Math.tan(Math.PI / 6), 1)
  })

  it('keeps labels off an obstacle (the mini-map), pushing inward or dropping', () => {
    // A tall box (the mini-map) covers both rows: the label under it drops.
    const miniMap = { x: 12, y: 12, width: 130, height: 130 }
    expect(placeMarks([mark({ id: 1, bearing: -20 })], 0, frame, size, 12, 4, [miniMap])).toEqual([])
    // Right of the box: untouched.
    const [right] = placeMarks([mark({ id: 2, bearing: 20 })], 0, frame, size, 12, 4, [miniMap])
    expect(right.y).toBe(12)
    // A short box only takes the edge row: the label moves inward.
    const pill = { x: 12, y: 12, width: 130, height: 20 }
    const [pushed] = placeMarks([mark({ id: 3, bearing: -20 })], 0, frame, size, 12, 4, [pill])
    expect(pushed.y).toBe(12 + 32)
  })

  it('applies the default inset along the edge and at the corner clamp', () => {
    const [ahead, corner, side] = placeMarks(
      [mark({ id: 1, bearing: 0 }), mark({ id: 2, bearing: 30 }), mark({ id: 3, bearing: 90 })],
      0,
      frame,
      size,
    )
    expect(ahead.y).toBe(LABEL_INSET_PX)
    expect(corner.x).toBe(400 - 100 - LABEL_INSET_PX)
    expect(side.x).toBe(400 - LABEL_INSET_PX - 100)
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

describe('fmtPopulation + tickPx + fmtMiles', () => {
  it('formats compact population', () => {
    expect(fmtPopulation(850)).toBe('850')
    expect(fmtPopulation(4213)).toBe('4.2k')
    expect(fmtPopulation(715_000)).toBe('715k')
    expect(fmtPopulation(2_700_000)).toBe('2.7M')
  })

  it('sizes the ring dot by tier, clamped', () => {
    expect(tickPx(1)).toBe(tickPx(2))
    expect(tickPx(3)).toBeGreaterThan(tickPx(2))
    expect(tickPx(5)).toBeGreaterThan(tickPx(3))
    expect(tickPx(0)).toBeLessThan(tickPx(1)) // hamlets smallest
    expect(tickPx(9)).toBe(tickPx(5))
  })

  it('formats compact miles', () => {
    expect(fmtMiles(1609.344)).toBe('1.0 mi')
    expect(fmtMiles(16_200)).toBe('10 mi')
    expect(fmtMiles(200_000)).toBe('124 mi')
  })
})

describe('labelSize', () => {
  const near = { tier: 2, distanceM: 1000, name: 'Alma' }
  it('scales with proximity', () => {
    const far = { ...near, distanceM: 30_000 }
    expect(labelSize(near).width).toBeGreaterThan(labelSize(far).width)
    expect(labelSize(near).height).toBeGreaterThan(labelSize(far).height)
  })
  it('floors short names at the meta line width', () => {
    expect(labelSize({ ...near, name: 'A' }).width).toBe(labelSize({ ...near, name: 'Al' }).width)
  })
  it('paints cities bigger than villages of the same name', () => {
    const village = labelSize({ tier: 2, distanceM: 1e9, name: 'Springfield' })
    const city = labelSize({ tier: 5, distanceM: 1e9, name: 'Springfield' })
    expect(city.width).toBeGreaterThan(village.width)
    expect(city.height).toBeGreaterThan(village.height)
    expect(CITY_NAME_PX).toBeGreaterThan(0)
  })
})

describe('currentTown', () => {
  const place = (id: number, tier: number, km: number, population = 1000): NearbyPlace => ({
    id,
    name: `p${id}`,
    summary: null,
    lat: 0,
    lon: 0,
    population,
    tier,
    region: null,
    country: null,
    distance_m: km * 1000,
    bearing_deg: 0,
  })

  it('is null with no candidates', () => {
    expect(currentTown([], null)).toBeNull()
  })

  it('picks the candidate the van is deepest inside, not the nearest point', () => {
    const village = place(1, 1, 1.4) // 1.4 of 1.5 km → ratio 0.93
    const city = place(2, 4, 5) // 5 of 15 km → ratio 0.33
    expect(currentTown([village, city], null)).toEqual({ place: city, inside: true })
  })

  it('falls back to the nearest candidate as "near" when none contains the van', () => {
    const a = place(1, 1, 4)
    const b = place(2, 2, 6)
    expect(currentTown([a, b], null)).toEqual({ place: a, inside: false })
  })

  it('keeps the incumbent out to the leave band', () => {
    const inc = place(1, 2, 3.3) // 3.3 of 3 km → ratio 1.1: out for a newcomer, in for the incumbent
    expect(currentTown([inc], null)).toEqual({ place: inc, inside: false })
    expect(currentTown([inc], 1)).toEqual({ place: inc, inside: true })
    expect(currentTown([place(1, 2, 3.6)], 1)).toEqual({ place: place(1, 2, 3.6), inside: false })
  })
})

describe('regionAbbrev + placeLabel', () => {
  it('abbreviates US states and Canadian provinces by name', () => {
    expect(regionAbbrev('Pennsylvania')).toBe('PA')
    expect(regionAbbrev('District of Columbia')).toBe('DC')
    expect(regionAbbrev('Ontario')).toBe('ON')
  })
  it('passes unmapped regions through and blanks a missing one', () => {
    expect(regionAbbrev('Chihuahua')).toBe('Chihuahua')
    expect(regionAbbrev(null)).toBe('')
  })
  it('labels a place with its region when known', () => {
    expect(placeLabel({ name: 'Philadelphia', region: 'Pennsylvania' })).toBe('Philadelphia, PA')
    expect(placeLabel({ name: 'Nowhere', region: null })).toBe('Nowhere')
  })
})

describe('hamlets (tier 0)', () => {
  const row = (id: number, tier: number, km: number, population: number | null): NearbyPlace => ({
    id,
    name: `h${id}`,
    summary: null,
    lat: 0,
    lon: 0,
    population,
    tier,
    region: 'Georgia',
    country: 'US',
    distance_m: km * 1000,
    bearing_deg: 0,
  })

  it('is the current town only when no populated town contains the van', () => {
    const hamlet = row(1, 0, 1.0, null) // 1.0 of 2 km → inside
    const town = row(2, 2, 16, 1050) // Jeffersonville-style, 16 km → not inside
    expect(currentTown([hamlet, town], null)).toEqual({ place: hamlet, inside: true })
    const city = row(3, 4, 5, 700_000) // 5 of 15 km → the city beats its own neighbourhood
    expect(currentTown([hamlet, town, city], null)).toEqual({ place: city, inside: true })
  })

  it('can be the nearest "near" candidate', () => {
    const hamlet = row(1, 0, 3, null)
    const town = row(2, 2, 16, 1050)
    expect(currentTown([hamlet, town], null)).toEqual({ place: hamlet, inside: false })
  })

  it('earns a ring mark within HAMLET_RING_KM at village priority, nearest first', () => {
    const near = row(1, 0, 1, null)
    const far = row(3, 0, 5, null) // outside the 4 km hamlet ring radius
    const village = row(2, 2, 3, 1050)
    const marks = selectMarksFn([far, village, near], newCompassState())
    expect(marks.map((m) => m.id)).toEqual([1, 2])
    expect(marks[0].population).toBeNull()
    expect(tickPx(0)).toBeLessThan(tickPx(1))
  })
})
