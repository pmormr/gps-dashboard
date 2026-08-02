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
