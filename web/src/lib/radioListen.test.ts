import { describe, expect, it } from 'vitest'

import { whepUrl } from './radioListen'

describe('whepUrl', () => {
  it('targets MediaMTX WebRTC port on the same host, whep suffix', () => {
    expect(whepUrl({ protocol: 'http:', hostname: '192.168.42.178' })).toBe(
      'http://192.168.42.178:8889/radio/whep',
    )
  })

  it('preserves the page protocol', () => {
    expect(whepUrl({ protocol: 'https:', hostname: 'pi.local' })).toBe(
      'https://pi.local:8889/radio/whep',
    )
  })
})
