import { describe, expect, it } from 'vitest'

import { whepEndpoint } from './whep'

describe('whepEndpoint', () => {
  it('targets MediaMTX WebRTC port on the same host, whep suffix', () => {
    expect(whepEndpoint('radio', { protocol: 'http:', hostname: '192.168.42.178' })).toBe(
      'http://192.168.42.178:8889/radio/whep',
    )
  })

  it('preserves the page protocol and takes any path', () => {
    expect(whepEndpoint('cam-front', { protocol: 'https:', hostname: 'pi.local' })).toBe(
      'https://pi.local:8889/cam-front/whep',
    )
  })
})
