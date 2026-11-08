/**
 * Persisted Drive HUD preferences — the town-compass density knobs the user
 * tunes from the dash (how many ring marks, how far in from the screen edge
 * they sit). Same shape as `mapPrefs.ts`: pure, store-free, versioned
 * localStorage key, field-wise validation so one bad field can't poison the
 * rest. The `drive` store (`stores/drive.svelte.ts`) seeds from and writes
 * back to this.
 */

export interface DrivePrefs {
  /** Total ring marks (metros + towns + villages); the compass slot cap. */
  ringCap: number
  /** Label distance in from the ring frame's edge, px. */
  labelInset: number
}

/** Slider ranges — the parser rejects anything outside them. */
export const RING_CAP_RANGE = { min: 6, max: 30 } as const
export const LABEL_INSET_RANGE = { min: 4, max: 32 } as const

export const DEFAULT_DRIVE_PREFS: Readonly<DrivePrefs> = {
  ringCap: 14,
  labelInset: 12,
}

/** localStorage key — versioned: bump when the shape or a field's range changes. */
export const DRIVE_PREFS_KEY = 'drive-hud.v1'

function int(v: unknown, lo: number, hi: number, fallback: number): number {
  return typeof v === 'number' && Number.isInteger(v) && v >= lo && v <= hi ? v : fallback
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
