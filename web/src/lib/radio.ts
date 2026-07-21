/** Pure radio display helpers for the `/radio` view. */

/** The RAWSTR value the ID-5100 manual pins to S9 (0000=S0, 0170=S9, §13-17). */
export const RAWSTR_S9 = 170

/** The waveform bar-height encoding ceiling (server stores 0..255 per bar). */
const WAVEFORM_MAX = 255

/**
 * Normalize a stored waveform envelope to 0..1 bar fractions for rendering.
 *
 * The API already decodes the JSON to `number[]`; this coerces null/absent to an
 * empty array and clamps each bar into [0, 1] so a stray value can't overflow the
 * strip. Pure so the strip component stays presentational.
 */
export function parseWaveform(waveform: number[] | null | undefined): number[] {
  if (!waveform) return []
  return waveform.map((v) => Math.max(0, Math.min(1, v / WAVEFORM_MAX)))
}

/**
 * SVG path data for a waveform as centered vertical bars in a `0 0 N 100` viewBox.
 *
 * One `<path>` renders the whole strip (cheap DOM even at hundreds of bars); the
 * player draws a second path from a played prefix to color it. Each bar sits in a
 * 1-unit slot (0.8 wide, 0.1 gap each side), height = sample × 100 with a 2-unit
 * floor so silence keeps a hairline. Pure so the strip stays presentational.
 */
export function barsPath(samples: number[]): string {
  let d = ''
  for (let i = 0; i < samples.length; i++) {
    const h = Math.max(samples[i] * 100, 2)
    const y = ((100 - h) / 2).toFixed(2)
    d += `M${i + 0.1} ${y}h0.8v${h.toFixed(2)}h-0.8z`
  }
  return d
}

/** Clamp a value into [0, 1]. */
function unit(x: number): number {
  return Math.max(0, Math.min(1, x))
}

/**
 * Playhead x-position (px) for a play time over a waveform of pixel width `w`.
 *
 * Returns 0 for a non-positive duration (nothing to scrub).
 */
export function cursorX(t: number, dur: number, w: number): number {
  return dur > 0 ? unit(t / dur) * w : 0
}

/**
 * Seek target (seconds) for a click at x (px) on a waveform of pixel width `w`.
 *
 * Returns 0 for a non-positive width (an unmeasured element).
 */
export function seekTime(x: number, w: number, dur: number): number {
  return w > 0 ? unit(x / w) * dur : 0
}

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
