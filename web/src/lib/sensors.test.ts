import { describe, expect, it } from 'vitest'

import type { MetricMeta } from './api'
import { decodeCoded, formatValue } from './sensors'

const base: MetricMeta = {
  label: '',
  unit: '',
  dec: 0,
  chart: false,
  color: '#000',
  convert: null,
  y_range: null,
  group: '',
  smooth: 0,
  codec: null,
  codes: null,
}

describe('decodeCoded', () => {
  const ENUM = { '0': 'Idle', '1': 'Charging', '2': 'Discharging' }
  const BITS = { '0': 'OK', '65536': 'under-volt', '262144': 'throttled', '4': 'throttled (now)' }

  it('looks up enum values', () => {
    expect(decodeCoded('enum', 1, ENUM)).toBe('Charging')
    expect(decodeCoded('enum', 2, ENUM)).toBe('Discharging')
  })

  it('falls back to the raw number for unknown enum codes', () => {
    expect(decodeCoded('enum', 9, ENUM)).toBe('9')
  })

  it('decodes 0 to the all-clear label for bitmasks', () => {
    expect(decodeCoded('bitmask', 0, BITS)).toBe('OK')
  })

  it('ORs together every set bitmask flag', () => {
    // 0x50000 = under-volt (0x10000) + throttled (0x40000), both sticky since-boot.
    expect(decodeCoded('bitmask', 0x50000, BITS)).toBe('under-volt, throttled')
  })

  it('matches only fully-set masks', () => {
    expect(decodeCoded('bitmask', 0x4, BITS)).toBe('throttled (now)')
    expect(decodeCoded('bitmask', 0x10000, BITS)).toBe('under-volt')
  })
})

describe('formatValue with a codec', () => {
  const meta = { state: { ...base, codec: 'enum', codes: { '1': 'Charging' } } }

  it('renders decoded text with no unit or alt', () => {
    expect(formatValue(meta, 'state', 1)).toEqual({ text: 'Charging', unit: '', alt: null })
  })

  it('still returns an em dash for null', () => {
    expect(formatValue(meta, 'state', null).text).toBe('—')
  })
})
