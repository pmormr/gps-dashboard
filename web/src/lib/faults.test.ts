import { describe, expect, it } from 'vitest'

import type { Status } from './api'
import { activeFaults, gnssFault } from './faults'

/** A minimal status payload: only the fields the fault derivation reads. */
function status(gnss_link: string | null, fixAgeS: number | null): Status {
  const now = Date.parse('2026-10-04T12:00:00.000Z')
  return {
    now: new Date(now).toISOString(),
    gnss_link,
    location:
      fixAgeS == null
        ? null
        : {
            timestamp: new Date(now - fixAgeS * 1000).toISOString(),
            lat: 39.5,
            lon: -105.1,
            speed: 0,
            altitude: 0,
            track: 0,
            mode: 3,
          },
  } as Status
}

describe('gnssFault', () => {
  it('is undefined for a healthy or never-reported link', () => {
    expect(gnssFault(status('ok', 1))).toBeUndefined()
    expect(gnssFault(status(null, null))).toBeUndefined()
  })

  it('names a dead RF path with its duration and the first thing to check', () => {
    expect(gnssFault(status('no_signal', 13 * 3600 + 34 * 60))).toBe(
      'No satellite signal for 13h 34m — check antenna',
    )
  })

  it('omits the duration when there has never been a fix', () => {
    expect(gnssFault(status('no_fix', null))).toBe('No GPS fix')
  })

  it('ignores an unknown state rather than inventing a fault', () => {
    expect(gnssFault(status('mystery', 500))).toBeUndefined()
  })
})

describe('activeFaults', () => {
  it('is empty when nothing is wrong', () => {
    expect(activeFaults(status('ok', 1))).toEqual([])
  })

  it('routes the GNSS fault to the gpsd drill-in', () => {
    expect(activeFaults(status('offline', 600))).toEqual([
      { id: 'gnss', message: 'GPS logger not reporting for 10m', to: '/gpsd' },
    ])
  })
})
