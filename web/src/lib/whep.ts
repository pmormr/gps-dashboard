/** Shared WHEP live-stream client for the MediaMTX hub (R10 in .claude/modules/radio.md).
 *
 * One receive-only WebRTC path for every live feed the hub fans out — radio
 * audio (`radio-stream.service`) and the van's Dahua cameras (on-demand RTSP
 * proxies; plans/cameras-plan.md). A bare `fetch` + `RTCPeerConnection`, no
 * signaling library: non-trickle, gather host ICE candidates (the offline LAN
 * needs no STUN), POST the full SDP offer to the WHEP endpoint, apply the
 * answer. Media then flows peer-to-peer from MediaMTX's ICE port straight to the
 * browser; only this handshake crosses HTTP, and only to the same host as the
 * app (a different port = MediaMTX).
 *
 * Callers pick the media kinds: radio requests `['audio']`, cameras `['video']`.
 */

/** MediaMTX WebRTC port — `webrtcAddress` in `deploy/mediamtx.yml`. WHEP lives
 *  at `http://<host>:8889/<path>/whep`; the app is served on a different port,
 *  so a WHEP fetch is always cross-origin to the same host. */
const WEBRTC_PORT = 8889

/** ICE gathering fallback: some browsers never fire `complete` when only host
 *  candidates exist, so cap the wait and send whatever we have. */
const ICE_TIMEOUT_MS = 3000

/** A track kind to receive, one recvonly transceiver each. */
export type WhepMedia = 'audio' | 'video'

/**
 * The WHEP endpoint URL for a MediaMTX path, derived from the page location.
 *
 * Same host as the app, MediaMTX's WebRTC port. Pure so it can be unit-tested;
 * defaults to the live `window.location`.
 *
 * @param path The MediaMTX path name (e.g. `radio`, `cam-front`).
 * @param loc The page location (protocol + hostname).
 */
export function whepEndpoint(
  path: string,
  loc: { protocol: string; hostname: string } = window.location,
): string {
  return `${loc.protocol}//${loc.hostname}:${WEBRTC_PORT}/${path}/whep`
}

/** A live receive-only WHEP session. */
export interface WhepSession {
  /** The inbound media stream — attach it to an `<audio>`/`<video>` `srcObject`. */
  stream: MediaStream
  /** Tear down the peer connection (stops media). Idempotent. */
  close: () => void
}

/** Options for {@link startWhep}. */
export interface WhepOptions {
  /** Media kinds to receive — one recvonly transceiver each (`['audio']`,
   *  `['video']`, or both for a cam with a mic). */
  media: WhepMedia[]
  /** Called when the peer connection drops on its own (failed/disconnected) —
   *  distinct from a caller-initiated {@link WhepSession.close}. */
  onClosed?: () => void
  /** Message for the thrown error when the hub is unreachable (the handshake
   *  fetch throws). Defaults to a generic hub message. */
  unreachableMessage?: string
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
 * Open a receive-only WHEP session against a live hub stream.
 *
 * @param url WHEP endpoint (see {@link whepEndpoint}).
 * @param opts Media kinds, drop callback, and error text.
 * @throws Error with a user-facing message on an unreachable hub or a refused
 *   handshake.
 */
export async function startWhep(url: string, opts: WhepOptions): Promise<WhepSession> {
  const pc = new RTCPeerConnection()
  for (const kind of opts.media) pc.addTransceiver(kind, { direction: 'recvonly' })

  const streamReady = new Promise<MediaStream>((resolve) => {
    pc.addEventListener('track', (e) => resolve(e.streams[0]))
  })
  pc.addEventListener('connectionstatechange', () => {
    if (['failed', 'disconnected'].includes(pc.connectionState)) opts.onClosed?.()
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
    throw new Error(opts.unreachableMessage ?? 'Could not reach the stream hub.')
  }
  if (!resp.ok) {
    pc.close()
    throw new Error(`The stream refused the connection (HTTP ${resp.status}).`)
  }

  await pc.setRemoteDescription({ type: 'answer', sdp: await resp.text() })
  const stream = await streamReady
  return { stream, close: () => pc.close() }
}
