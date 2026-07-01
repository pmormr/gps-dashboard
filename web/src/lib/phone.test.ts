import type { LineString, Point } from 'geojson'
import { describe, expect, it } from 'vitest'

import type { PhonePath, PhoneVisit } from './api'
import { MODE_COLORS, modeGroup, phonePathsToFC, phoneVisitsToFC } from './phone'

function pt(lon: number, lat: number, activity_type: string | null) {
  return { lon, lat, importance: 0, activity_type }
}

function path(points: ReturnType<typeof pt>[]): PhonePath {
  return { id: 1, start_time: 'a', end_time: 'b', n_points: points.length, points }
}

describe('modeGroup', () => {
  it('maps known activity types to their group', () => {
    expect(modeGroup('IN_PASSENGER_VEHICLE')).toBe('driving')
    expect(modeGroup('WALKING')).toBe('walking')
    expect(modeGroup('IN_FERRY')).toBe('transit')
  })

  it('falls back to unknown for null/unrecognized', () => {
    expect(modeGroup(null)).toBe('unknown')
    expect(modeGroup('SOMETHING_NEW')).toBe('unknown')
  })
})

describe('phonePathsToFC', () => {
  it('emits one feature for a single-mode path, colored by mode', () => {
    const fc = phonePathsToFC([
      path([pt(-77, 40, 'WALKING'), pt(-77.1, 40.1, 'WALKING'), pt(-77.2, 40.2, 'WALKING')]),
    ])
    expect(fc.features).toHaveLength(1)
    const f = fc.features[0]
    expect(f.properties?.color).toBe(MODE_COLORS.walking)
    expect((f.geometry as LineString).coordinates).toHaveLength(3)
  })

  it('splits into runs at a mode change, sharing the boundary vertex', () => {
    const fc = phonePathsToFC([
      path([
        pt(-77.0, 40.0, 'IN_PASSENGER_VEHICLE'),
        pt(-77.1, 40.1, 'IN_PASSENGER_VEHICLE'),
        pt(-77.2, 40.2, 'WALKING'),
        pt(-77.3, 40.3, 'WALKING'),
      ]),
    ])
    expect(fc.features).toHaveLength(2)
    const [drive, walk] = fc.features
    expect(drive.properties?.color).toBe(MODE_COLORS.driving)
    expect(walk.properties?.color).toBe(MODE_COLORS.walking)
    // The mode boundary vertex is shared so the polyline stays connected.
    const driveCoords = (drive.geometry as LineString).coordinates
    const walkCoords = (walk.geometry as LineString).coordinates
    expect(driveCoords.at(-1)).toEqual(walkCoords[0])
    expect(driveCoords.at(-1)).toEqual([-77.2, 40.2])
  })

  it('treats null activity as an unknown run', () => {
    const fc = phonePathsToFC([path([pt(-77, 40, null), pt(-77.1, 40.1, null)])])
    expect(fc.features[0].properties?.color).toBe(MODE_COLORS.unknown)
  })

  it('drops paths with fewer than two points', () => {
    expect(phonePathsToFC([path([pt(-77, 40, 'WALKING')])]).features).toHaveLength(0)
    expect(phonePathsToFC([{ id: 1, start_time: 'a', end_time: 'b', n_points: 0 }]).features).toHaveLength(0)
  })
})

describe('phoneVisitsToFC', () => {
  it('builds point features carrying a color and a prebuilt popup', () => {
    const visit: PhoneVisit = {
      id: 1,
      start_time: '2020-01-01T00:00:00.000Z',
      end_time: '2020-01-01T01:00:00.000Z',
      lat: 40,
      lon: -77,
      place_id: 'p1',
      semantic_type: 'HOME',
      probability: 0.9,
    }
    const fc = phoneVisitsToFC([visit])
    expect(fc.features).toHaveLength(1)
    const f = fc.features[0]
    expect((f.geometry as Point).coordinates).toEqual([-77, 40])
    expect(f.properties?.color).toBeTruthy()
    expect(f.properties?.popup).toContain('HOME')
  })
})
