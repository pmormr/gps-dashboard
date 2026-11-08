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
    const prefs = {
      ringCap: 22,
      labelInset: 20,
      miniMap: false,
      miniMapZoom: 8,
      zoomMin: 11.5,
      zoomHiMph: 40,
      zoomSlew: 1.2,
    }
    expect(parseDrivePrefs(JSON.stringify(prefs))).toEqual(prefs)
  })

  it('reads a v1 document written before the mini-map and camera fields existed', () => {
    expect(parseDrivePrefs(JSON.stringify({ ringCap: 20, labelInset: 16 }))).toEqual({
      ...DEFAULT_DRIVE_PREFS,
      ringCap: 20,
      labelInset: 16,
    })
  })

  it('drops a street-level mini-map zoom persisted before the regional re-range', () => {
    expect(parseDrivePrefs(JSON.stringify({ miniMapZoom: 16 }))).toEqual(DEFAULT_DRIVE_PREFS)
  })

  it('accepts fractional camera values only inside their ranges', () => {
    expect(parseDrivePrefs(JSON.stringify({ zoomMin: 9.5, zoomSlew: 0.3, miniMap: 'yes' }))).toEqual({
      ...DEFAULT_DRIVE_PREFS,
      zoomSlew: 0.3,
    })
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
