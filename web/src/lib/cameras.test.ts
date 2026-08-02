import { describe, expect, it } from 'vitest'

import type { Camera } from './api'
import { drivingCameras, livePath, snapshotUrl } from './cameras'

describe('snapshotUrl', () => {
  it('targets the proxy route with a cache-busting stamp', () => {
    expect(snapshotUrl('van-cam-front', 123)).toBe('/api/cameras/van-cam-front/snapshot?t=123')
  })
})

describe('livePath', () => {
  const cam: Camera = { node: 'van-cam-front', label: 'Front', path: 'cam-front', driving: false }

  it('uses the sub feed for the tile', () => {
    expect(livePath(cam, false)).toBe('cam-front')
  })

  it('uses the -hd feed when expanded', () => {
    expect(livePath(cam, true)).toBe('cam-front-hd')
  })
})

describe('drivingCameras', () => {
  const fleet: Camera[] = [
    { node: 'van-cam-front', label: 'Front', path: 'cam-front', driving: false },
    { node: 'van-cam-blind-left', label: 'Blind L', path: 'cam-blind-left', driving: true },
    { node: 'van-cam-rear', label: 'Rear', path: 'cam-rear', driving: true },
  ]

  it('keeps only the driving-flagged feeds, in order', () => {
    expect(drivingCameras(fleet).map((c) => c.node)).toEqual(['van-cam-blind-left', 'van-cam-rear'])
  })
})
