import { describe, expect, it } from 'vitest'

import { whepEndpoint } from './whep'

describe('whepEndpoint', () => {
  it('targets the same-origin nginx WHEP proxy', () => {
    expect(whepEndpoint('radio')).toBe('/whep/radio')
  })

  it('takes any MediaMTX path name', () => {
    expect(whepEndpoint('cam-front')).toBe('/whep/cam-front')
  })
})
