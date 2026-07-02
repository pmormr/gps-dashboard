/** Pure radio display helpers for the `/radio` view. */

/** The RAWSTR value the ID-5100 manual pins to S9 (0000=S0, 0170=S9, §13-17). */
export const RAWSTR_S9 = 170

/** A rendered S-meter reading: the S-unit label and the 0–100 bar fill. */
export interface SMeterReading {
  label: string
  pct: number
}

/**
 * Convert a Hamlib RAWSTR value (0–255) to calibrated S-units.
 *
 * The scale is linear S0–S9 across 0–170 per the manual's anchors; the meter
 * floor()s like a segment display (a reading between S8 and S9 shows S8).
 * Above 170 the manual gives no dB anchor, so anything past S9 is `S9+`.
 */
export function sMeter(raw: number | null | undefined): SMeterReading | null {
  if (raw == null) return null
  const clamped = Math.max(0, Math.min(255, raw))
  const pct = (clamped / 255) * 100
  if (clamped >= RAWSTR_S9) return { label: clamped > RAWSTR_S9 ? 'S9+' : 'S9', pct }
  return { label: `S${Math.floor((clamped / RAWSTR_S9) * 9)}`, pct }
}
