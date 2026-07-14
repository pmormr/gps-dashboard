import type { Point } from 'geojson'
import { describe, expect, it } from 'vitest'

import type { Place, PlaceCategory, PlaceKind } from './api'
import {
  placesToFC,
  CATEGORY_GROUPS,
  CATEGORY_META,
  categoryMeta,
  expandGroups,
  facetLabel,
  KIND_META,
  kindMeta,
  maxRankForZoom,
  placeMeta,
  searchResultsToFC,
  stripHtml,
} from './places'

function row(overrides: Partial<Place> = {}): Place {
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
    category: 'park',
    rank: 1,
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

describe('categoryMeta / placeMeta', () => {
  it('resolves every declared category', () => {
    for (const m of CATEGORY_META) expect(categoryMeta(m.category)).toBe(m)
  })

  it('falls back to a neutral entry for unmapped/null categories', () => {
    expect(categoryMeta(null).label).toBe('Other')
    expect(categoryMeta('mystery').label).toBe('Other')
  })

  it('prefers the per-kind meta for federal kinds', () => {
    expect(placeMeta(row())).toBe(kindMeta('park'))
  })

  it('falls back to the category meta for OSM kinds', () => {
    const osm = row({ source: 'osm', source_kind: 'amenity=cafe', category: 'food_drink' })
    expect(placeMeta(osm)).toBe(categoryMeta('food_drink'))
  })

  it('category colors differ (chip/pin readability)', () => {
    const colors = new Set(CATEGORY_META.map((m) => m.color))
    expect(colors.size).toBe(CATEGORY_META.length)
  })
})

describe('CATEGORY_GROUPS / expandGroups', () => {
  it('partitions CATEGORY_META: every category in exactly one group', () => {
    const grouped = CATEGORY_GROUPS.flatMap((g) => g.categories)
    expect(grouped.length).toBe(CATEGORY_META.length)
    expect(new Set(grouped).size).toBe(grouped.length)
    const declared = new Set<string>(CATEGORY_META.map((m) => m.category))
    for (const c of grouped) expect(declared.has(c)).toBe(true)
  })

  it('expands selected groups to their member categories', () => {
    expect(expandGroups(new Set(['stay']))).toEqual(['camping', 'lodging'])
    expect(expandGroups(new Set())).toEqual([])
    const all = expandGroups(new Set(CATEGORY_GROUPS.map((g) => g.key)))
    expect(all.length).toBe(CATEGORY_META.length)
  })

  it('group colors differ (legend readability)', () => {
    const colors = new Set(CATEGORY_GROUPS.map((g) => g.color))
    expect(colors.size).toBe(CATEGORY_GROUPS.length)
  })
})

describe('facetLabel', () => {
  it('uses the per-kind meta for federal kinds', () => {
    expect(facetLabel('campground')).toBe('Campgrounds')
  })

  it('humanizes OSM tag values', () => {
    expect(facetLabel('amenity=fast_food')).toBe('Fast food')
    expect(facetLabel('tourism=camp_site')).toBe('Camp site')
  })

  it('passes bare unknown kinds through capitalized', () => {
    expect(facetLabel('stream')).toBe('Stream')
  })
})

describe('maxRankForZoom', () => {
  it('admits deeper ranks as zoom increases, majors-only when zoomed out', () => {
    expect(maxRankForZoom(0)).toBe(1)
    expect(maxRankForZoom(8.9)).toBe(1)
    expect(maxRankForZoom(9)).toBe(2)
    expect(maxRankForZoom(12)).toBe(3)
    expect(maxRankForZoom(14)).toBe(4)
  })

  it('never admits rank 5 (micro furniture is search-only)', () => {
    expect(maxRankForZoom(22)).toBe(4)
  })
})

describe('placesToFC', () => {
  it('builds one pin per located row with id/kind/name/color properties', () => {
    const fc = placesToFC([row(), row({ id: 2, source_kind: 'campground', lat: 41, lon: -106 })])
    expect(fc.features).toHaveLength(2)
    const [park, camp] = fc.features
    expect((park.geometry as Point).coordinates).toEqual([-105.7, 40.4])
    expect(park.properties).toMatchObject({ id: 1, kind: 'park', name: 'Rocky Mountain' })
    expect(park.properties!.color).toBe(kindMeta('park').color)
    expect(camp.properties!.color).toBe(kindMeta('campground').color)
  })

  it('skips rows without a coordinate', () => {
    const fc = placesToFC([row({ lat: null, lon: null }), row({ id: 2 })])
    expect(fc.features.map((f) => f.properties!.id)).toEqual([2])
  })

  it('colors differ across kinds (legend readability)', () => {
    const colors = new Set(KIND_META.map((m) => m.color))
    expect(colors.size).toBe(KIND_META.length)
  })
})

describe('searchResultsToFC', () => {
  it('builds uncolored pins (the engine styles results uniformly) and skips nocoord rows', () => {
    const fc = searchResultsToFC([row(), row({ id: 2, lat: null, lon: null })])
    expect(fc.features.map((f) => f.properties!.id)).toEqual([1])
    expect(fc.features[0].properties).toEqual({
      id: 1,
      kind: 'park',
      name: 'Rocky Mountain',
      icon: 'poi:park',
    })
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
const _kinds: PlaceKind[] = KIND_META.map((m) => m.kind)
void _kinds
