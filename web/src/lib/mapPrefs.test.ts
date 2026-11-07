import { describe, expect, it } from 'vitest'

import { DEFAULT_STYLE_PREFS, mapGround, parseStylePrefs } from './mapPrefs'

describe('parseStylePrefs', () => {
  it('returns defaults for null, empty and garbage input', () => {
    expect(parseStylePrefs(null)).toEqual(DEFAULT_STYLE_PREFS)
    expect(parseStylePrefs('')).toEqual(DEFAULT_STYLE_PREFS)
    expect(parseStylePrefs('{not json')).toEqual(DEFAULT_STYLE_PREFS)
    expect(parseStylePrefs('42')).toEqual(DEFAULT_STYLE_PREFS)
  })

  it('round-trips a full document', () => {
    const prefs = {
      base: 'usgs',
      theme: 'light',
      terrain: true,
      exaggeration: 2.5,
      minorRoads: false,
      labelOffset: -3,
    } as const
    expect(parseStylePrefs(JSON.stringify(prefs))).toEqual(prefs)
  })

  it('fills missing fields from defaults, keeping the valid ones', () => {
    expect(parseStylePrefs(JSON.stringify({ theme: 'white' }))).toEqual({
      ...DEFAULT_STYLE_PREFS,
      theme: 'white',
    })
  })

  it('rejects unknown/out-of-range values per field', () => {
    const got = parseStylePrefs(
      JSON.stringify({
        base: 'bing',
        theme: 'sepia',
        terrain: 'yes',
        exaggeration: 40,
        minorRoads: 1,
        labelOffset: 2,
      }),
    )
    expect(got).toEqual(DEFAULT_STYLE_PREFS)
  })
})

describe('mapGround', () => {
  it('is dark only for the dark vector themes', () => {
    expect(mapGround('osm', 'dark')).toBe('dark')
    expect(mapGround('osm', 'black')).toBe('dark')
    expect(mapGround('osm', 'light')).toBe('light')
    expect(mapGround('osm', 'grayscale')).toBe('light')
  })
  it('treats the USGS raster as light whatever the vector theme says', () => {
    expect(mapGround('usgs', 'dark')).toBe('light')
  })
})
