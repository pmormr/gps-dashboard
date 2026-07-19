/**
 * The one GNSS constellation table: gpsd `gnssid` → display name, RINEX letter,
 * and marker colour. globe/skyplot/Sky all read their name+colour from here so
 * the palette can't drift three ways. The names mirror `common/gpsd.py`
 * (backend-side); keep the two in step.
 */

/** One constellation's identity: gpsd id, display name, RINEX letter, marker colour. */
export interface GnssMeta {
  gnssid: number
  name: string
  /** RINEX constellation letter, for satellite names (G01, E11, …). */
  letter: string
  /** CSS marker colour. */
  color: string
}

export const GNSS_META: readonly GnssMeta[] = [
  { gnssid: 0, name: 'GPS', letter: 'G', color: '#22c55e' },
  { gnssid: 1, name: 'SBAS', letter: 'S', color: '#94a3b8' },
  { gnssid: 2, name: 'Galileo', letter: 'E', color: '#f59e0b' },
  { gnssid: 3, name: 'BeiDou', letter: 'C', color: '#ef4444' },
  { gnssid: 5, name: 'QZSS', letter: 'J', color: '#a78bfa' },
  { gnssid: 6, name: 'GLONASS', letter: 'R', color: '#3b82f6' },
]

/** Fallback for a gnssid gpsd reports that isn't in the table. */
export const GNSS_OTHER = { name: 'Other', color: '#64748b' } as const

const BY_ID = new Map(GNSS_META.map((m) => [m.gnssid, m]))

/** Display name for a gnssid ('Other' if unknown). */
export function gnssName(gnssid: number): string {
  return BY_ID.get(gnssid)?.name ?? GNSS_OTHER.name
}

/** CSS marker colour for a gnssid (the Other grey if unknown). */
export function gnssColor(gnssid: number): string {
  return BY_ID.get(gnssid)?.color ?? GNSS_OTHER.color
}

/** Marker colour as a 0xRRGGBB number, for three.js materials. */
export function gnssColorInt(gnssid: number): number {
  return parseInt(gnssColor(gnssid).slice(1), 16)
}

/** RINEX constellation letter for a gnssid ('?' if unknown). */
export function gnssLetter(gnssid: number): string {
  return BY_ID.get(gnssid)?.letter ?? '?'
}

/** name → CSS colour, including Other (skyplot keys its markers by name). */
export const GNSS_COLOR_BY_NAME: Record<string, string> = Object.fromEntries([
  ...GNSS_META.map((m) => [m.name, m.color]),
  [GNSS_OTHER.name, GNSS_OTHER.color],
])

/** Legend/stacking order — deliberately not gnssid order (GLONASS sorts up front). */
export const GNSS_ORDER = ['GPS', 'GLONASS', 'Galileo', 'BeiDou', 'QZSS', 'SBAS', 'Other']
