/**
 * Persisted Drive HUD preferences — the knobs the user tunes from the dash:
 * town-compass density (ring cap, label inset), the mini-map, and the
 * follow camera's speed→zoom curve. Same shape as `mapPrefs.ts`: pure,
 * store-free, versioned localStorage key, field-wise validation so one bad
 * field can't poison the rest. The `drive` store (`stores/drive.svelte.ts`)
 * seeds from and writes back to this. The follow-camera defaults are the
 * `follow.ts` constants, so an untouched slider reproduces the tuned road feel.
 */

import { SPEED_HI_MS, ZOOM_MIN, ZOOM_SLEW_PER_S } from './follow'

export interface DrivePrefs {
  /** Total ring marks (metros + towns + villages); the compass slot cap. */
  ringCap: number
  /** Label distance in from the ring frame's edge, px. */
  labelInset: number
  /** Render the top-left compass as a live mini-map (off = the plain rose). */
  miniMap: boolean
  /** The mini-map's fixed zoom level. */
  miniMapZoom: number
  /** Follow-camera zoom at and above the full-zoom-out speed (how far out at speed). */
  zoomMin: number
  /** Speed (mph) at which the follow camera reaches `zoomMin`. */
  zoomHiMph: number
  /** Max follow-camera zoom change per second — the zoom "ramp". */
  zoomSlew: number
}

/** Slider ranges — the parser rejects anything outside them. */
export const RING_CAP_RANGE = { min: 6, max: 30 } as const
export const LABEL_INSET_RANGE = { min: 4, max: 32 } as const
export const MINI_MAP_ZOOM_RANGE = { min: 10, max: 18 } as const
export const ZOOM_MIN_RANGE = { min: 10, max: 16, step: 0.5 } as const
export const ZOOM_HI_MPH_RANGE = { min: 20, max: 80 } as const
export const ZOOM_SLEW_RANGE = { min: 0.2, max: 3, step: 0.1 } as const

export const DEFAULT_DRIVE_PREFS: Readonly<DrivePrefs> = {
  ringCap: 14,
  labelInset: 12,
  miniMap: true,
  miniMapZoom: 16,
  zoomMin: ZOOM_MIN,
  zoomHiMph: Math.round(SPEED_HI_MS * 2.236936),
  zoomSlew: ZOOM_SLEW_PER_S,
}

/** localStorage key — versioned: bump when the shape or a field's range changes. */
export const DRIVE_PREFS_KEY = 'drive-hud.v1'

function int(v: unknown, lo: number, hi: number, fallback: number): number {
  return typeof v === 'number' && Number.isInteger(v) && v >= lo && v <= hi ? v : fallback
}

function num(v: unknown, lo: number, hi: number, fallback: number): number {
  return typeof v === 'number' && Number.isFinite(v) && v >= lo && v <= hi ? v : fallback
}

function bool(v: unknown, fallback: boolean): boolean {
  return typeof v === 'boolean' ? v : fallback
}

/**
 * Parse a stored prefs document, field by field: anything missing, malformed
 * or out of range falls back to its default.
 */
export function parseDrivePrefs(raw: string | null): DrivePrefs {
  const d = DEFAULT_DRIVE_PREFS
  let obj: Record<string, unknown> = {}
  try {
    const parsed: unknown = raw ? JSON.parse(raw) : null
    if (parsed && typeof parsed === 'object') obj = parsed as Record<string, unknown>
  } catch {
    /* garbage → defaults */
  }
  return {
    ringCap: int(obj.ringCap, RING_CAP_RANGE.min, RING_CAP_RANGE.max, d.ringCap),
    labelInset: int(obj.labelInset, LABEL_INSET_RANGE.min, LABEL_INSET_RANGE.max, d.labelInset),
    miniMap: bool(obj.miniMap, d.miniMap),
    miniMapZoom: int(obj.miniMapZoom, MINI_MAP_ZOOM_RANGE.min, MINI_MAP_ZOOM_RANGE.max, d.miniMapZoom),
    zoomMin: num(obj.zoomMin, ZOOM_MIN_RANGE.min, ZOOM_MIN_RANGE.max, d.zoomMin),
    zoomHiMph: int(obj.zoomHiMph, ZOOM_HI_MPH_RANGE.min, ZOOM_HI_MPH_RANGE.max, d.zoomHiMph),
    zoomSlew: num(obj.zoomSlew, ZOOM_SLEW_RANGE.min, ZOOM_SLEW_RANGE.max, d.zoomSlew),
  }
}

/** The persisted prefs, or defaults when storage is unavailable/empty. */
export function loadDrivePrefs(): DrivePrefs {
  try {
    return parseDrivePrefs(localStorage.getItem(DRIVE_PREFS_KEY))
  } catch {
    return { ...DEFAULT_DRIVE_PREFS }
  }
}

/** Persist the prefs; silently a no-op where storage is unavailable. */
export function saveDrivePrefs(prefs: DrivePrefs): void {
  try {
    localStorage.setItem(DRIVE_PREFS_KEY, JSON.stringify(prefs))
  } catch {
    /* private mode / quota — the in-memory store still works */
  }
}
