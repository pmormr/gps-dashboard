/**
 * Persisted map style preferences — base map, vector theme, terrain, label
 * density — the slice of the Layers store that should survive a reload (the
 * dash-mounted phone reloads; losing a light theme every time is the bug).
 *
 * Pure and store-free so the *engine* can seed from it too: `map.ts` boots
 * with these values (and warms the right theme's style), the `layers` store
 * initialises from the same call, and the two agree without a handshake.
 * Data-layer toggles (drone/phone/places) are deliberately session-scoped.
 */

import { BASEMAP_THEMES, DEFAULT_BASEMAP_THEME, type BasemapTheme } from './labels'

export type BaseLayer = 'osm' | 'usgs'

export interface StylePrefs {
  base: BaseLayer
  theme: BasemapTheme
  terrain: boolean
  /** Terrain exaggeration factor (the panel slider's 0.5–8 range). */
  exaggeration: number
  /** Minor street names on the vector base. */
  minorRoads: boolean
  /** POI density zoom offset (the Data-layers slider's −4…0 range). */
  labelOffset: number
}

export const DEFAULT_STYLE_PREFS: Readonly<StylePrefs> = {
  base: 'osm',
  theme: DEFAULT_BASEMAP_THEME,
  terrain: false,
  exaggeration: 1.3,
  minorRoads: true,
  labelOffset: -1,
}

/** localStorage key — versioned: bump when the shape or a field's range changes. */
export const STYLE_PREFS_KEY = 'map-style.v1'

const THEME_IDS: ReadonlySet<string> = new Set(BASEMAP_THEMES.map((t) => t.id))

function num(v: unknown, lo: number, hi: number, fallback: number): number {
  return typeof v === 'number' && Number.isFinite(v) && v >= lo && v <= hi ? v : fallback
}

function bool(v: unknown, fallback: boolean): boolean {
  return typeof v === 'boolean' ? v : fallback
}

/**
 * Parse a stored prefs document, field by field: anything missing, malformed
 * or out of range falls back to its default rather than poisoning the rest.
 */
export function parseStylePrefs(raw: string | null): StylePrefs {
  const d = DEFAULT_STYLE_PREFS
  let obj: Record<string, unknown> = {}
  try {
    const parsed: unknown = raw ? JSON.parse(raw) : null
    if (parsed && typeof parsed === 'object') obj = parsed as Record<string, unknown>
  } catch {
    /* garbage → defaults */
  }
  return {
    base: obj.base === 'usgs' ? 'usgs' : 'osm',
    theme:
      typeof obj.theme === 'string' && THEME_IDS.has(obj.theme) ? (obj.theme as BasemapTheme) : d.theme,
    terrain: bool(obj.terrain, d.terrain),
    exaggeration: num(obj.exaggeration, 0.5, 8, d.exaggeration),
    minorRoads: bool(obj.minorRoads, d.minorRoads),
    labelOffset: num(obj.labelOffset, -4, 0, d.labelOffset),
  }
}

/** The persisted prefs, or defaults when storage is unavailable/empty. */
export function loadStylePrefs(): StylePrefs {
  try {
    return parseStylePrefs(localStorage.getItem(STYLE_PREFS_KEY))
  } catch {
    return { ...DEFAULT_STYLE_PREFS }
  }
}

/** Persist the prefs; silently a no-op where storage is unavailable. */
export function saveStylePrefs(prefs: StylePrefs): void {
  try {
    localStorage.setItem(STYLE_PREFS_KEY, JSON.stringify(prefs))
  } catch {
    /* private mode / quota — the in-memory store still works */
  }
}
