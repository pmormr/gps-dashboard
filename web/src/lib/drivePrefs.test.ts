import { describe, expect, it } from 'vitest'

import { DEFAULT_DRIVE_PREFS, parseDrivePrefs } from './drivePrefs'

describe('parseDrivePrefs', () => {
  it('returns defaults for null, empty and garbage input', () => {
    expect(parseDrivePrefs(null)).toEqual(DEFAULT_DRIVE_PREFS)
    expect(parseDrivePrefs('')).toEqual(DEFAULT_DRIVE_PREFS)
    expect(parseDrivePrefs('{not json')).toEqual(DEFAULT_DRIVE_PREFS)
    expect(parseDrivePrefs('42')).toEqual(DEFAULT_DRIVE_PREFS)
  })

  it('round-trips a full document', () => {
    const prefs = { ringCap: 22, labelInset: 20 }
    expect(parseDrivePrefs(JSON.stringify(prefs))).toEqual(prefs)
  })

  it('fills missing fields from defaults, keeping the valid ones', () => {
    expect(parseDrivePrefs(JSON.stringify({ ringCap: 8 }))).toEqual({
      ...DEFAULT_DRIVE_PREFS,
      ringCap: 8,
    })
  })

  it('rejects out-of-range, fractional and non-numeric values field by field', () => {
    expect(parseDrivePrefs(JSON.stringify({ ringCap: 99, labelInset: 16 }))).toEqual({
      ...DEFAULT_DRIVE_PREFS,
      labelInset: 16,
    })
    expect(parseDrivePrefs(JSON.stringify({ ringCap: 10.5, labelInset: '8' }))).toEqual(
      DEFAULT_DRIVE_PREFS,
    )
  })
})
