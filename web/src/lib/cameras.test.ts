import { describe, expect, it } from 'vitest'

import { livePath, snapshotUrl } from './cameras'

describe('snapshotUrl', () => {
  it('targets the proxy route with a cache-busting stamp', () => {
    expect(snapshotUrl('van-cam-front', 123)).toBe('/api/cameras/van-cam-front/snapshot?t=123')
  })
})

describe('livePath', () => {
  const cam = { node: 'van-cam-front', label: 'Front', path: 'cam-front' }

  it('uses the sub feed for the tile', () => {
    expect(livePath(cam, false)).toBe('cam-front')
  })

  it('uses the -hd feed when expanded', () => {
    expect(livePath(cam, true)).toBe('cam-front-hd')
  })
})
