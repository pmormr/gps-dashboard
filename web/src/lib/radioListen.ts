/** WHEP live-listen client for the radio audio stream (R10 in the radio plan).
 *
 * Listens to the MediaMTX `radio` path (published by `radio-stream.service`)
 * with a bare `fetch` + `RTCPeerConnection` — no signaling library. Non-trickle:
 * gather host ICE candidates (the offline LAN needs no STUN), POST the full SDP
 * offer to the WHEP endpoint, apply the answer. Media then flows peer-to-peer
 * from MediaMTX's ICE port straight to the browser; only this handshake crosses
 * HTTP, and only to the same host as the app (a different port = MediaMTX).
 */

/** MediaMTX WebRTC port — `webrtcAddress` in `deploy/mediamtx.yml`. WHEP lives
 *  at `http://<host>:8889/<path>/whep`; the app is served on a different port,
 *  so listening is always a cross-origin fetch to the same host. */
const WEBRTC_PORT = 8889

/** The MediaMTX path radio-stream publishes to (`paths.radio` in mediamtx.yml). */
const STREAM_PATH = 'radio'

/** ICE gathering fallback: some browsers never fire `complete` when only host
 *  candidates exist, so cap the wait and send whatever we have. */
const ICE_TIMEOUT_MS = 3000

/**
 * The WHEP endpoint URL for the radio stream, derived from the page location.
 *
 * Same host as the app, MediaMTX's WebRTC port. Pure so it can be unit-tested;
 * defaults to the live `window.location`.
 */
export function whepUrl(loc: { protocol: string; hostname: string } = window.location): string {
  return `${loc.protocol}//${loc.hostname}:${WEBRTC_PORT}/${STREAM_PATH}/whep`
}

/** A live receive-only WHEP session. */
export interface ListenSession {
  /** The inbound audio stream — attach it to an `<audio>` element to play. */
  stream: MediaStream
  /** Tear down the peer connection (stops media). Idempotent. */
  close: () => void
}

/** Resolve once ICE gathering completes, or after `ICE_TIMEOUT_MS` as a fallback. */
function iceComplete(pc: RTCPeerConnection): Promise<void> {
  if (pc.iceGatheringState === 'complete') return Promise.resolve()
  return new Promise((resolve) => {
    const done = (): void => {
      pc.removeEventListener('icegatheringstatechange', check)
      resolve()
    }
    const check = (): void => {
      if (pc.iceGatheringState === 'complete') done()
    }
    pc.addEventListener('icegatheringstatechange', check)
    window.setTimeout(done, ICE_TIMEOUT_MS)
  })
}

/**
 * Open a receive-only WHEP session against the live radio stream.
 *
 * @param url WHEP endpoint (defaults to the derived {@link whepUrl}).
 * @param onClosed Called when the peer connection drops on its own (failed/
 *   disconnected) — distinct from a caller-initiated {@link ListenSession.close}.
 * @throws Error with a user-facing message on an unreachable hub or a refused
 *   handshake.
 */
export async function startListen(
  url: string = whepUrl(),
  onClosed?: () => void,
): Promise<ListenSession> {
  const pc = new RTCPeerConnection()
  pc.addTransceiver('audio', { direction: 'recvonly' })

  const streamReady = new Promise<MediaStream>((resolve) => {
    pc.addEventListener('track', (e) => resolve(e.streams[0]))
  })
  pc.addEventListener('connectionstatechange', () => {
    if (['failed', 'disconnected'].includes(pc.connectionState)) onClosed?.()
  })

  await pc.setLocalDescription(await pc.createOffer())
  await iceComplete(pc)

  let resp: Response
  try {
    resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp' },
      body: pc.localDescription?.sdp ?? '',
    })
  } catch {
    pc.close()
    throw new Error('Could not reach the audio hub — is radio-stream running?')
  }
  if (!resp.ok) {
    pc.close()
    throw new Error(`The stream refused the connection (HTTP ${resp.status}).`)
  }

  await pc.setRemoteDescription({ type: 'answer', sdp: await resp.text() })
  const stream = await streamReady
  return { stream, close: () => pc.close() }
}
