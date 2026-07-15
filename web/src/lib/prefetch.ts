/**
 * Idle-time raster tile prefetcher — the tile math for warming what the user
 * is about to need. Two predictions: zooming back out (parent tiles of the
 * current view, several levels up — the tight-zoom → overview gesture) and
 * panning (a one-tile ring around the visible cover). Raster (USGS) only:
 * the vector/DEM PMTiles are byte-ranged archives a plain GET can't warm,
 * and their in-session cache already covers zoom-out. Every prefetched
 * raster tile also lands in the Pi's disk cache via the tile proxy, so
 * browsing an area while online doubles as offline precache.
 *
 * Pure module (no MapLibre import); the engine wires it to the map's idle
 * event and does the actual fetching.
 */

/** One raster tile address. */
export interface TileCoord {
  z: number
  x: number
  y: number
}

/** Viewport bounds in degrees (MapLibre getBounds() shape). */
export interface Bounds {
  west: number
  south: number
  east: number
  north: number
}

/** Options for {@link prefetchTiles}. */
export interface PrefetchOptions {
  /** The raster layer's max zoom — tiles above it don't exist. */
  maxzoom: number
  /** How many parent zoom levels to warm for the zoom-out case. */
  parentLevels?: number
  /** Hard cap on the returned tile count (parents come first). */
  cap?: number
}

/** Web-Mercator lon/lat → tile coordinate at zoom z (clamped to the grid). */
export function lonLatToTile(lon: number, lat: number, z: number): TileCoord {
  const n = 2 ** z
  const clamp = (v: number): number => Math.min(n - 1, Math.max(0, v))
  const x = Math.floor(((lon + 180) / 360) * n)
  const latRad = (lat * Math.PI) / 180
  const y = Math.floor(((1 - Math.asinh(Math.tan(latRad)) / Math.PI) / 2) * n)
  return { z, x: clamp(x), y: clamp(y) }
}

/** Inclusive tile range covering the bounds at zoom z. */
function coverRange(b: Bounds, z: number): { x0: number; x1: number; y0: number; y1: number } {
  const nw = lonLatToTile(b.west, b.north, z)
  const se = lonLatToTile(b.east, b.south, z)
  return { x0: nw.x, x1: se.x, y0: nw.y, y1: se.y }
}

/**
 * The prefetch set for a settled viewport: parents of the visible cover at
 * z-1..z-parentLevels (zoom-out readiness, nearest level first), then a
 * one-tile pan ring around the cover at z. The visible cover itself is
 * excluded — the map already loaded it. Deduped; capped (parents first, so
 * a small cap sacrifices ring tiles before zoom-out readiness).
 */
export function prefetchTiles(b: Bounds, zoom: number, opts: PrefetchOptions): TileCoord[] {
  const { maxzoom, parentLevels = 3, cap = 48 } = opts
  const z = Math.max(0, Math.min(Math.floor(zoom), maxzoom))
  const out: TileCoord[] = []
  const seen = new Set<string>()
  const add = (t: TileCoord): void => {
    const key = `${t.z}/${t.x}/${t.y}`
    if (seen.has(key)) return
    seen.add(key)
    out.push(t)
  }

  for (let d = 1; d <= parentLevels && z - d >= 0; d++) {
    const pz = z - d
    const r = coverRange(b, pz)
    for (let x = r.x0; x <= r.x1; x++) for (let y = r.y0; y <= r.y1; y++) add({ z: pz, x, y })
  }

  const n = 2 ** z
  const r = coverRange(b, z)
  for (let x = r.x0 - 1; x <= r.x1 + 1; x++) {
    for (let y = r.y0 - 1; y <= r.y1 + 1; y++) {
      if (x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1) continue // visible
      if (y < 0 || y >= n) continue // past the poles
      add({ z, x: ((x % n) + n) % n, y }) // wrap x across the antimeridian
    }
  }

  return out.slice(0, cap)
}
