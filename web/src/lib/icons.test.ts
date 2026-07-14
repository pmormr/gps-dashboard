import { describe, expect, it } from 'vitest'

import spriteJson from '../../../static/vendor/basemap/sprite-poi/poi.json'

import {
  BASEMAP_KINDS,
  CATEGORY_ICONS,
  CATEGORY_META,
  decodeFeatureId,
  encodeFeatureId,
  FALLBACK_ICON,
  FEDERAL_KIND_ICONS,
  rowIcon,
  spriteRef,
} from './icons'

// The committed sprite is the ground truth: every icon name the language
// references must exist there, or a mark/pin silently renders without a glyph.
const sprite = spriteJson as Record<string, { sdf?: boolean }>

describe('icon language', () => {
  it('every referenced icon exists in the committed sprite', () => {
    const referenced = new Set<string>([
      FALLBACK_ICON,
      ...Object.values(CATEGORY_ICONS),
      ...Object.values(FEDERAL_KIND_ICONS),
      ...Object.values(BASEMAP_KINDS).map((m) => m.icon),
    ])
    const missing = [...referenced].filter((name) => !(name in sprite))
    expect(missing).toEqual([])
  })

  it('sprite icons are SDF (tintable)', () => {
    expect(Object.values(sprite).every((e) => e.sdf)).toBe(true)
  })

  it('every basemap kind maps to a known category', () => {
    const categories = new Set(CATEGORY_META.map((m) => m.category as string))
    const unknown = Object.entries(BASEMAP_KINDS).filter(([, m]) => !categories.has(m.category))
    expect(unknown).toEqual([])
  })

  it('rowIcon prefers federal kind, falls back to category, then marker', () => {
    expect(rowIcon({ source_kind: 'visitorcenter', category: 'attraction' })).toBe('ranger-station')
    expect(rowIcon({ source_kind: 'amenity=cafe', category: 'food_drink' })).toBe('restaurant')
    expect(rowIcon({ source_kind: 'amenity=weird', category: null })).toBe(FALLBACK_ICON)
  })

  it('spriteRef qualifies with the poi sprite id', () => {
    expect(spriteRef('fuel')).toBe('poi:fuel')
  })
})

describe('planetiler feature-id codec', () => {
  it('round-trips nodes, ways, and relations', () => {
    for (const sid of ['node/6401093626', 'way/967607248', 'relation/12345']) {
      const fid = encodeFeatureId(sid)
      expect(fid).not.toBeNull()
      expect(decodeFeatureId(fid!)).toBe(sid)
    }
  })

  it('matches the observed tile encodings', () => {
    // Verified against real tiles: The UPS Store (node) and the Denver
    // Municipal Animal Shelter (way) — decoded from real archive tiles.
    expect(decodeFeatureId(17598587138042)).toBe('node/6401093626')
    expect(decodeFeatureId(35185339696080)).toBe('way/967607248')
  })

  it('rejects unknown shapes', () => {
    expect(decodeFeatureId(999 * 2 ** 44)).toBeNull()
    expect(encodeFeatureId('gnis/12345')).toBeNull()
    expect(encodeFeatureId('node/notanumber')).toBeNull()
  })
})
