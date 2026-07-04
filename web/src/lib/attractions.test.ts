import type { Point } from 'geojson'
import { describe, expect, it } from 'vitest'

import type { Attraction, AttractionKind } from './api'
import {
  attractionsToFC,
  KIND_META,
  kindMeta,
  kindsAtZoom,
  MIN_DETAIL_ZOOM,
  stripHtml,
} from './attractions'

function row(overrides: Partial<Attraction> = {}): Attraction {
  return {
    id: 1,
    source: 'nps',
    source_kind: 'park',
    source_id: 'X',
    park_code: 'romo',
    name: 'Rocky Mountain',
    lat: 40.4,
    lon: -105.7,
    summary: null,
    synced_at: '2026-07-03T00:00:00.000Z',
    ...overrides,
  }
}

describe('kindMeta', () => {
  it('resolves every declared kind', () => {
    for (const m of KIND_META) expect(kindMeta(m.kind)).toBe(m)
  })

  it('falls back to a neutral entry for unknown kinds', () => {
    const meta = kindMeta('ridb-facility')
    expect(meta.label).toBe('ridb-facility')
    expect(meta.color).toBeTruthy()
  })
})

describe('kindsAtZoom', () => {
  const all = KIND_META.map((m) => m.kind)

  it('passes every kind through at or past the gate', () => {
    expect(kindsAtZoom(all, MIN_DETAIL_ZOOM)).toEqual(all)
    expect(kindsAtZoom(all, 12)).toEqual(all)
  })

  it('keeps only the container kinds below the gate', () => {
    expect(kindsAtZoom(all, MIN_DETAIL_ZOOM - 1)).toEqual(['park', 'recarea'])
  })

  it('is empty below the gate when the containers are deselected', () => {
    const noContainers = all.filter((k) => k !== 'park' && k !== 'recarea')
    expect(kindsAtZoom(noContainers, 3)).toEqual([])
  })
})

describe('attractionsToFC', () => {
  it('builds one pin per located row with id/kind/name/color properties', () => {
    const fc = attractionsToFC([row(), row({ id: 2, source_kind: 'campground', lat: 41, lon: -106 })])
    expect(fc.features).toHaveLength(2)
    const [park, camp] = fc.features
    expect((park.geometry as Point).coordinates).toEqual([-105.7, 40.4])
    expect(park.properties).toMatchObject({ id: 1, kind: 'park', name: 'Rocky Mountain' })
    expect(park.properties!.color).toBe(kindMeta('park').color)
    expect(camp.properties!.color).toBe(kindMeta('campground').color)
  })

  it('skips rows without a coordinate', () => {
    const fc = attractionsToFC([row({ lat: null, lon: null }), row({ id: 2 })])
    expect(fc.features.map((f) => f.properties!.id)).toEqual([2])
  })

  it('colors differ across kinds (legend readability)', () => {
    const colors = new Set(KIND_META.map((m) => m.color))
    expect(colors.size).toBe(KIND_META.length)
  })
})

describe('stripHtml', () => {
  it('drops tags and normalizes whitespace', () => {
    expect(stripHtml('<p>Join a ranger</p>\n<p>at the  <b>overlook</b>.</p>')).toBe(
      'Join a ranger at the overlook.',
    )
  })

  it('decodes the common entities', () => {
    expect(stripHtml('Trails &amp; Rails &#39;26 &lt;free&gt;&nbsp;event')).toBe(
      "Trails & Rails '26 <free> event",
    )
  })

  it('passes plain text through', () => {
    expect(stripHtml('No markup here.')).toBe('No markup here.')
  })
})

// Type-level guard: the declared kinds stay in sync with the API union.
const _kinds: AttractionKind[] = KIND_META.map((m) => m.kind)
void _kinds
