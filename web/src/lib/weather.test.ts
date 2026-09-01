import { describe, expect, it } from 'vitest'

import { formatBytes, frameAgeLabel } from './weather'

describe('formatBytes', () => {
  it('renders bytes below 1 kB', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1023)).toBe('1023 B')
  })

  it('renders kB below 1 MB', () => {
    expect(formatBytes(1024)).toBe('1 kB')
    expect(formatBytes(22_000)).toBe('21 kB')
    expect(formatBytes(1_048_575)).toBe('1024 kB')
  })

  it('renders MB with one decimal below 10 MB, whole above', () => {
    expect(formatBytes(1_048_576)).toBe('1.0 MB')
    expect(formatBytes(2_700_000)).toBe('2.6 MB')
    expect(formatBytes(10_485_760)).toBe('10 MB')
    expect(formatBytes(52_428_800)).toBe('50 MB')
  })
})

describe('frameAgeLabel', () => {
  const now = 1_788_270_000_000

  it('reads "now" under a minute', () => {
    expect(frameAgeLabel(now - 30_000, now)).toBe('now')
  })

  it('reads minutes under an hour', () => {
    expect(frameAgeLabel(now - 9 * 60_000, now)).toBe('9 min ago')
  })

  it('reads hours + minutes past an hour', () => {
    expect(frameAgeLabel(now - 90 * 60_000, now)).toBe('1h 30m ago')
    expect(frameAgeLabel(now - 120 * 60_000, now)).toBe('2h ago')
  })
})
