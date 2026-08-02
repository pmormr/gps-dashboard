/** Camera grid helpers (plans/cameras-plan.md): thumbnail URLs + hub-path picks.
 *
 * The grid polls the server-side snapshot proxy (a small JPEG per camera — near-
 * zero idle, HaLow-friendly); tapping a tile goes live over WHEP against the same
 * MediaMTX hub path. The pure URL/path builders live here (unit-tested); the view
 * owns the refresh timer and the `<video>` lifecycle.
 */

import type { Camera } from './api'

/** Thumbnail refresh cadence (ms). Gentle on HaLow: the sub-stream stills are
 *  small, but four of them add up, so poll slowly. */
export const SNAPSHOT_REFRESH_MS = 5000

/** Driving-wall tile reconnect delay (ms) after a live feed drops. Short: this
 *  is a safety view, so a blip should heal quickly rather than stay dark. */
export const CAM_RETRY_MS = 3000

/** Left-to-right arrangement of the driving wall: mirror-left · rear · mirror-
 *  right — the driver's own spatial layout. Cams flagged `driving` but absent
 *  here fall to the end, so a newly-flagged feed degrades gracefully rather
 *  than vanishing. */
export const DRIVING_ORDER: string[] = [
  'van-cam-blind-left',
  'van-cam-rear',
  'van-cam-blind-right',
]

/**
 * The driving-mode subset of a camera list — the blind-spot + rear feeds, in
 * {@link DRIVING_ORDER} (the mirror layout). The server owns *which* cams via the
 * `driving` flag (the front is excluded: the windshield is the front view); the
 * order is the wall's spatial arrangement, so it lives here beside the layout.
 *
 * @param cameras The full viewable fleet from `/api/cameras`.
 */
export function drivingCameras(cameras: Camera[]): Camera[] {
  const rank = (node: string): number => {
    const i = DRIVING_ORDER.indexOf(node)
    return i === -1 ? DRIVING_ORDER.length : i
  }
  return cameras.filter((c) => c.driving).sort((a, b) => rank(a.node) - rank(b.node))
}

/**
 * The snapshot proxy URL for a camera node, cache-busted so each poll refetches.
 *
 * @param node The camera's fleet node (e.g. `van-cam-front`).
 * @param bust A monotonic stamp (ms) shared by the grid so all tiles refresh together.
 */
export function snapshotUrl(node: string, bust: number): string {
  return `/api/cameras/${node}/snapshot?t=${bust}`
}

/**
 * The MediaMTX hub path to go live on: the 720p `-hd` feed when expanded, else
 * the D1 sub feed the grid tile mirrors.
 *
 * @param camera The camera (its `path` is the sub/glance path).
 * @param hd Whether to use the higher-res expand feed.
 */
export function livePath(camera: Camera, hd: boolean): string {
  return hd ? `${camera.path}-hd` : camera.path
}

// ── Driving 180° wall alignment ──
//
// The three driving feeds butt edge-to-edge into one continuous strip. Each is a
// CSS-transform window into its feed (zoom to crop the fisheye/overlap edges, pan
// the visible sector, mirror if needed) plus a share of the strip width. CSS can't
// un-warp fisheye radial distortion, so this is a best-effort visual alignment, not
// a true stitch. The transform is geometric (fixed by camera mounting, not scene
// content), so values tuned once — via the /cameras/drive Align mode — carry over
// to the road; they persist per-device and get baked back into DEFAULT_ALIGN.

/** One camera's window + width in the 180° strip. */
export interface CamAlign {
  /** Relative width in the strip (flex-grow). */
  weight: number
  /** Zoom (≥1) — crops the fisheye/overlap edges. */
  scale: number
  /** Horizontal pan as a fraction of the tile (−1..1); shifts the visible window. */
  panX: number
  /** Vertical pan as a fraction of the tile (−1..1). */
  panY: number
  /** Mirror horizontally (none of the van cams are mirrored by default). */
  flip: boolean
}

/** No-op window: full feed, equal width, no mirror. */
export const IDENTITY_ALIGN: CamAlign = { weight: 1, scale: 1, panX: 0, panY: 0, flip: false }

/** Seeds fitted against a pulled-forward capture composited through this exact
 *  transform model (blind cams zoomed to trim fisheye + the inner van wedge, rear
 *  anchored near 1× and widest). A best-effort visual match — the tuner refines the
 *  magnitudes (they depend on the device's cell aspect); the signs are correct. */
export const DEFAULT_ALIGN: Record<string, CamAlign> = {
  'van-cam-blind-left': { weight: 1, scale: 1.15, panX: 0.1, panY: 0.05, flip: false },
  'van-cam-rear': { weight: 1.5, scale: 1.06, panX: 0, panY: -0.02, flip: false },
  'van-cam-blind-right': { weight: 1, scale: 1.25, panX: 0.1, panY: 0.05, flip: false },
}

/** The seed alignment for a node — its default, or identity for an unknown cam. */
export function defaultAlign(node: string): CamAlign {
  return DEFAULT_ALIGN[node] ?? IDENTITY_ALIGN
}

/** Round to `d` decimals, stripping FP noise (unary + drops trailing zeros). */
function round(v: number, d: number): number {
  return +v.toFixed(d)
}

/** The CSS `transform` for a tile's video: flip, zoom, pan, about the center. */
export function alignTransform(a: CamAlign): string {
  const sx = round(a.scale * (a.flip ? -1 : 1), 3)
  const sy = round(a.scale, 3)
  return `translate(${round(a.panX * 100, 2)}%, ${round(a.panY * 100, 2)}%) scale(${sx}, ${sy})`
}

/** localStorage key for the tuned per-device alignment. Versioned: bumped whenever
 *  the fitted defaults change, so devices start from the new seed rather than a stale
 *  experimental value. (v3: strip locked to the 720p stream; defaults are 16:9-fitted.) */
const ALIGN_KEY = 'gps.cam.align.v3'

/** Load the stored alignment map (node → align), `{}` when unset/unavailable. */
export function loadAlign(): Record<string, Partial<CamAlign>> {
  try {
    const raw = localStorage.getItem(ALIGN_KEY)
    return raw ? (JSON.parse(raw) as Record<string, Partial<CamAlign>>) : {}
  } catch {
    return {}
  }
}

/** Persist the alignment map; a no-op if storage is unavailable (private mode/quota). */
export function saveAlign(map: Record<string, CamAlign>): void {
  try {
    localStorage.setItem(ALIGN_KEY, JSON.stringify(map))
  } catch {
    // storage unavailable — alignment just won't persist across reloads
  }
}

/** Drop the stored override so alignment falls back to the fitted defaults (Reset). */
export function clearAlign(): void {
  try {
    localStorage.removeItem(ALIGN_KEY)
  } catch {
    // storage unavailable — nothing to clear
  }
}

/** Seed alignment for the given nodes: stored values over the per-node defaults,
 *  merged field-by-field so a partial/old stored entry still fills its gaps. */
export function mergedAlign(nodes: string[]): Record<string, CamAlign> {
  const stored = loadAlign()
  const map: Record<string, CamAlign> = {}
  for (const node of nodes) map[node] = { ...defaultAlign(node), ...stored[node] }
  return map
}
