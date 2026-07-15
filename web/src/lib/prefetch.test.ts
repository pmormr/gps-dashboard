import { describe, expect, it } from 'vitest'

import { lonLatToTile, prefetchTiles, type Bounds } from './prefetch'

// A one-tile viewport: exactly tile z10 x216 y387 (east of Boulder).
const ONE_TILE: Bounds = { west: -104.0, south: 39.95, east: -103.9, north: 40.15 }

describe('lonLatToTile', () => {
  it('maps the origin to the grid center', () => {
    expect(lonLatToTile(0, 0, 1)).toEqual({ z: 1, x: 1, y: 1 })
    expect(lonLatToTile(-1, 1, 1)).toEqual({ z: 1, x: 0, y: 0 })
  })

  it('matches a known slippy-map tile', () => {
    // boulder.co ≈ (-105.27, 40.01) → z10 tile 212/387 (openstreetmap slippy math)
    expect(lonLatToTile(-105.27, 40.01, 10)).toEqual({ z: 10, x: 212, y: 387 })
  })

  it('clamps at the grid edges instead of overflowing', () => {
    expect(lonLatToTile(180, 0, 2).x).toBe(3)
    expect(lonLatToTile(-180, 0, 2).x).toBe(0)
    expect(lonLatToTile(0, 89.9, 2).y).toBe(0)
    expect(lonLatToTile(0, -89.9, 2).y).toBe(3)
  })
})

describe('prefetchTiles', () => {
  it('returns parents nearest-level-first, then the pan ring', () => {
    const tiles = prefetchTiles(ONE_TILE, 10, { maxzoom: 16 })
    // Parents: one covering tile per level z9, z8, z7.
    expect(tiles[0].z).toBe(9)
    expect(tiles.filter((t) => t.z === 9).length).toBeGreaterThanOrEqual(1)
    expect(tiles.some((t) => t.z === 8)).toBe(true)
    expect(tiles.some((t) => t.z === 7)).toBe(true)
    // Ring: 8 neighbors of a single-tile cover.
    expect(tiles.filter((t) => t.z === 10)).toHaveLength(8)
  })

  it('never returns the visible cover itself', () => {
    const tiles = prefetchTiles(ONE_TILE, 10, { maxzoom: 16 })
    const cover = lonLatToTile(-103.85, 40.05, 10)
    expect(tiles.some((t) => t.z === 10 && t.x === cover.x && t.y === cover.y)).toBe(false)
  })

  it('floors fractional zoom and respects the layer maxzoom', () => {
    const frac = prefetchTiles(ONE_TILE, 10.7, { maxzoom: 16 })
    expect(Math.max(...frac.map((t) => t.z))).toBe(10)
    // A z18-sized viewport with the layer capped at 16: nothing above z16.
    const tiny: Bounds = { west: -103.851, south: 40.05, east: -103.85, north: 40.051 }
    const capped = prefetchTiles(tiny, 18, { maxzoom: 16 })
    expect(Math.max(...capped.map((t) => t.z))).toBe(16)
  })

  it('caps the set with parents surviving over ring tiles', () => {
    const tiles = prefetchTiles(ONE_TILE, 10, { maxzoom: 16, cap: 3 })
    expect(tiles).toHaveLength(3)
    expect(tiles.every((t) => t.z < 10)).toBe(true)
  })

  it('drops ring tiles past the poles and wraps across the antimeridian', () => {
    const polar: Bounds = { west: -10, south: 84, east: 10, north: 85 }
    const z = 4
    const polarTiles = prefetchTiles(polar, z, { maxzoom: 16, parentLevels: 0 })
    expect(polarTiles.every((t) => t.y >= 0 && t.y < 2 ** z)).toBe(true)

    const edge: Bounds = { west: -179.9, south: 39, east: -178, north: 40 }
    const edgeTiles = prefetchTiles(edge, z, { maxzoom: 16, parentLevels: 0 })
    expect(edgeTiles.every((t) => t.x >= 0 && t.x < 2 ** z)).toBe(true)
    expect(edgeTiles.some((t) => t.x === 2 ** z - 1)).toBe(true) // the wrapped column
  })

  it('never returns tiles below z0', () => {
    const world: Bounds = { west: -170, south: -80, east: 170, north: 80 }
    const tiles = prefetchTiles(world, 1, { maxzoom: 16 })
    expect(tiles.every((t) => t.z >= 0)).toBe(true)
  })
})
