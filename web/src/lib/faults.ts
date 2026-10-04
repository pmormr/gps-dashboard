/**
 * Cross-tab fault derivation from the `/api/status` aggregate. One place decides
 * what counts as a fault and how it reads, so Home's panels and the shell's
 * every-tab banner can't disagree. A new fault source is one more entry in
 * {@link activeFaults}.
 */
import type { Status } from './api'
import { fmtDurationSecs } from './geo'

/** A fault the shell surfaces on every view. */
export interface Fault {
  /** Stable key (list identity). */
  id: string
  message: string
  /** Route that explains the fault (the banner navigates here). */
  to: string
}

/** `gnss_link` fault states → what happened, plus the first thing to check. */
const GNSS_LINK_FAULTS: Record<string, { text: string; hint?: string }> = {
  no_signal: { text: 'No satellite signal', hint: 'check antenna' },
  no_fix: { text: 'No GPS fix' },
  offline: { text: 'GPS logger not reporting' },
}

/**
 * The GNSS fault line for a status payload, or undefined when the link is healthy
 * (or has never reported). Carries how long since the last raw fix when one exists
 * ('No satellite signal for 13h 34m — check antenna').
 */
export function gnssFault(s: Status): string | undefined {
  const fault = s.gnss_link ? GNSS_LINK_FAULTS[s.gnss_link] : undefined
  if (!fault) return undefined
  const since = s.location
    ? ` for ${fmtDurationSecs((Date.parse(s.now) - Date.parse(s.location.timestamp)) / 1000, { days: true })}`
    : ''
  return `${fault.text}${since}${fault.hint ? ` — ${fault.hint}` : ''}`
}

/** Every fault currently active in a status payload, in display order. */
export function activeFaults(s: Status): Fault[] {
  const faults: Fault[] = []
  const gnss = gnssFault(s)
  if (gnss) faults.push({ id: 'gnss', message: gnss, to: '/gpsd' })
  return faults
}
