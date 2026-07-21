# Van Cameras → Dashboard Plan

> Living plan. **Started 2026-07-21.** Bring the van's four Dahua IP cameras into
> the Van OS SPA for **glancing** (primary) and a low-latency **driving mode**
> (fast-follow). Reuses the radio's **MediaMTX** hub (R10, `.claude/modules/radio.md`)
> as the single aggregation point — the "hub later proxies the Dahua cams" future
> named there. Distinct from `plans/streaming-platform-plan.md` (that is the
> hill-climb *event* cams, edge Pis pushing SRT; this is the van's own IP cams,
> pulled from). Both share the one hub.

## Context

Four Dahua cams are already on the LAN and health-polled by `sensors/dahua_reader.py`
(telemetry only — no video). Goal: view them from the dashboard. Usage is
**glance-first** (park-and-check, often from home over the HaLow bridge), with an
occasional **driving mode** (blind-spots + rear). Paul currently drives off the
NVR's HDMI output, whose latency is bad — a WebRTC path off the low-res streams
should beat it (camera → MediaMTX remux → WebRTC targets sub-500 ms; ~150–300 ms
on the LAN vs the HDMI decode/composite buffer).

The fleet (`FLEET` in `sensors/dahua_reader.py`; Hikvision `.55/.56` out of scope — ISAPI, not recording):

| Node | IP | Model | Notes |
|---|---|---|---|
| van-cam-front | 192.168.42.51 | N43AJ52 (4MP) | mic (PCM on main) |
| van-cam-blind-left | 192.168.42.52 | DH-IPC-MBW4431N-M12 2.8mm | fisheye, no mic |
| van-cam-blind-right | 192.168.42.53 | DH-IPC-MBW4431N-M12 2.8mm | fisheye, no mic |
| van-cam-rear | 192.168.42.54 | N43AN52 (4MP) | mic (G.711 a-law on main) |
| van-nvr | 192.168.42.50 | (records main streams) | HTTPS/RPC2; **not** a video source here |

## Decisions

- **C1 — One hub, reuse the radio's MediaMTX.** Add camera `paths:` to the existing
  `mediamtx` service (v1.19.2, `deploy/mediamtx.yml`); do not stand up a second server.
  Config is read from the deploy checkout, so edits deploy on push (the hook restarts
  `mediamtx` when `mediamtx.yml` changes).
- **C2 — Pull direct from each camera, not via the NVR.** `source: rtsp://…` per cam.
  Doesn't touch NVR recording load; no channel-mapping indirection.
- **C3 — Three-tier stream layout; main stays H.265.** Per camera:
  - `subtype=0` main — **H.265**, 2688×1520@30, **untouched** (NVR recording only, never
    hits the browser). Kept H.265 because the NVR disk is small (~496 GB) and already
    100 % full, continuous 4-cam record ⇒ only ~2–4 days retention; H.264 would roughly
    halve it. WebRTC can't decode H.265 anyway.
  - `subtype=1` sub — **H.264**, D1 704×480 **@15**, GOP 15 (1 s), ~512 k CBR → the
    **glance** feed (light enough for HaLow/home).
  - `subtype=2` third — **H.264**, 720p 1280×720 **@30**, GOP 30 (1 s), ~2 Mbps CBR →
    the **expand + driving** feed (local LAN).
- **C4 — On-demand pull** (`sourceOnDemand`) so idle load is zero; cameras/LAN carry
  nothing until a viewer attaches. Cost = ~1–2 s connect latency on open. **Driving mode
  pre-warms** its feeds (opens them on entry) to avoid paying that mid-drive.
- **C5 — Secret injection, never in git.** Password stays out of the committed yaml:
  MediaMTX `${GPS_DAHUA_PASSWORD}` interpolation + `EnvironmentFile=-/etc/default/gps-dahua`
  on `mediamtx.service` (file already exists, root-600, holds `GPS_DAHUA_PASSWORD`).
- **C6 — Glance-first UX.** Cameras is a new **top-level tab** (Radio-style, `SECTIONS`
  registry). Grid of the four cams; tap → live; expand → 720p. Driving mode is a
  fast-follow.
- **C7 — Multicast rejected.** It buys nothing here: the hub pulls each cam exactly once
  (it *is* the fan-out), and hub→browser is WebRTC = inherently unicast. Multicast only
  helps many independent clients pulling a camera directly — the topology the hub avoids.
  (`rtspTransports` already lists `multicast` for any future LAN RTSP multiviewer.)
- **C8 — Low-latency levers:** short GOP (1 s, done in C3), pre-warm driving streams (C4),
  low decode res (D1/720p). These target the HDMI-latency complaint directly.

## Operational traps

- **MediaMTX v1.19.2 does not interpolate `${VAR}` in config values (learned Phase 1).**
  C5 assumed it would; it doesn't — a `${…}` left in a `source:` URL reaches Go's
  `url.Parse` literally (the `{`/`}` are illegal) and the service **crash-loops** with
  `'rtsp://…' is not a valid URL`, taking the radio stream down with it. Verified with a
  throwaway config: `${MYTESTVAR}` came through untouched. Fix: `mediamtx.yml` is a
  **template**; `deploy/mediamtx-run.sh` substitutes the placeholder at service start and
  runs the hub on a rendered copy in the unit's tmpfs `RuntimeDirectory` (0600).
- **The password wasn't the problem — the braces were (red herring, learned Phase 1).**
  The crash log redacted `admin:<pw>@` and `<pw>` happened to also match the literal
  `${…}`, so it *looked* like a bad password char. It wasn't: the current password is
  URL-valid. The wrapper still **URL-percent-encodes** the password it substitutes —
  purely defensive, so a future rotation to a genuinely-unsafe char (`@ / : #` space)
  can't reintroduce the crash. Single secret var `GPS_DAHUA_PASSWORD` (root-600
  `/etc/default/gps-dahua`, the raw form the dahua_reader also uses for digest auth); the
  wrapper derives the encoding, so there's no second var to keep in sync.

## Operational traps (learned during Phase 0)

- **`snapshot.cgi` ignores `subtype` — always main-res (learned Phase 3).** On this
  firmware `snapshot.cgi?channel=1&subtype={0,1,2}` all return the ~600 kB 2688×1520 main
  still (verified on the wire), so there's no small-still param. The snapshot proxy fetches
  the main still on the van LAN and **downscales it server-side** (Pillow → ~480 px, ~20 kB)
  so only the thumbnail crosses HaLow — the whole point of the thumbnail grid. Same family
  as the `setConfig` silent-param trap below.
- **Dahua `setConfig` needs URL-encoded brackets.** On this firmware
  `configManager.cgi?action=setConfig&Encode[0]…` with **literal** brackets returns an
  empty body and **silently changes nothing**; `%5B`/`%5D`-encoded brackets return `OK`
  / HTTP 200 and apply. `getConfig` works with literal brackets. Verify every write with
  a read-back, not the response body.
- **`ffprobe` echoes RTSP creds** (`rtsp://admin:PASS@…`) in its error output. Any camera
  shell work must redact — pipe through `sed -E 's#admin:[^@]*@#admin:***@#g'`.
- RTSP URL format: `rtsp://admin:<pw>@<host>:554/cam/realmonitor?channel=1&subtype=N`.
- Snapshot (for the Phase 3 option-b thumbnail grid): `/cgi-bin/snapshot.cgi?channel=1`
  (digest auth) — proxy server-side so creds stay off the browser.

## Phase 0 — Camera reconfig — DONE (2026-07-21)

- [x] Probed all four: every stream was H.265 (main + sub) — the WebRTC blocker
      (MediaMTX does not transcode). Caps confirm H.264 + an unused third stream (720p max)
      on every model.
- [x] Reconfigured all four to the C3 layout (sub→H.264 D1@15 GOP15; enabled third→H.264
      720p@30 GOP30 2 Mbps CBR; main left H.265). Applied via `setConfig` with encoded
      brackets; **verified on the wire** with `ffprobe` (subtype=1 = h264 704×480,
      subtype=2 = h264 1280×720 on all four).

## Phase 1 — Hub (MediaMTX paths + secret) — DONE (2026-07-21)

Schema verified against the v1.19.2 reference `mediamtx.yml`: `sourceOnDemand` (def
`false`), `sourceOnDemandCloseAfter`/`sourceOnDemandStartTimeout` (def `10s` each),
`rtspTransport` (per-path, `automatic|udp|multicast|tcp` — distinct from the global
`rtspTransports` list). Left the 10s close/start-timeout defaults implicit.

- [x] `deploy/mediamtx.yml`: added 8 on-demand pull paths — `cam-<pos>` (sub, glance) +
      `cam-<pos>-hd` (third, 720p expand/driving) for front/blind-left/blind-right/rear.
      `rtspTransport: tcp`. Validated the YAML parses (8 paths, `${…}` preserved literal).
- [x] `deploy/mediamtx.service`: added `EnvironmentFile=-/etc/default/gps-dahua` +
      `RuntimeDirectory=mediamtx` (0700), and pointed `ExecStart` at the render wrapper.
- [x] `deploy/mediamtx-run.sh`: render wrapper — MediaMTX won't interpolate `${VAR}`, so
      it reads the single `GPS_DAHUA_PASSWORD` from the secret file, URL-encodes it,
      substitutes the placeholder, and execs the hub on the rendered
      `/run/mediamtx/mediamtx.yml` (validated on spare ports: config loads, listeners
      start, no URL error).
- [x] Confirmed `mediamtx.service` is enabled + active (config change just needs restart).
- [x] Deployed + verified on the wire. All four cams pull H.264 at the C3 resolutions
      (`ffprobe` via the hub: sub = 704×480@15, `-hd` = 1280×720@30). On-demand confirmed
      in the journal (`[RTSP source] started on demand` on connect, torn down on
      disconnect). WHEP endpoint live (`/cam-front/whep` → 204 on preflight; browser video
      pending the Phase 2 client). Rendered config verified: 0 unsubstituted placeholders.

## Phase 2 — Video WHEP client — DONE (2026-07-21)

- [x] Factored `web/src/lib/whep.ts`: the generic client `startWhep(url, { media,
      onClosed, unreachableMessage })` + `whepEndpoint(path, loc)`, media kinds chosen by
      the caller (one recvonly transceiver each). Kept the bare `fetch` +
      `RTCPeerConnection`, host-ICE-only, no-STUN approach verbatim. `radioListen.ts`
      folded in and removed; `Radio.svelte` now calls `startWhep(whepEndpoint('radio'),
      { media: ['audio'], … })` — behavior-identical (same URL/flow/error text). Test moved
      to `whep.test.ts`. Typecheck + 160 tests + build all green.
- [x] Video path exercised end-to-end by the Phase 3 grid's tap-to-live (`media: ['video']`).

## Phase 3 — Cameras tab (glance-first) — DONE (2026-07-21)

**Grid rendering: option (b) — JPEG thumbnails first** (chosen for the from-home HaLow
glance, the primary use). Placement: a **10th top-level tab** (📷), per the plan's
glance-first intent (accepts the narrow-phone label crowding — nav notes).

- [x] Top-level **Cameras** tab wired (`routes.ts` NAV + routes; `Cameras.svelte`). 2×2 grid.
- [x] **Server:** extracted `sensors/fleet.py` (no-dep `Device`/`FLEET`, so the web app
      reuses fleet identity without pulling in MQTT — `dahua_reader`/`dahua_probe`/tests now
      import from it). `api/routes/cameras.py`: `GET /api/cameras` (node/label/hub-path;
      hosts stay server-side) + `GET /api/cameras/<node>/snapshot` (server-side digest to
      `snapshot.cgi?channel=1`, **downscaled with Pillow** to a ~480 px/~20 kB thumbnail —
      subtype is ignored, see trap; 404 unknown/NVR, 502 refused/non-image, 503 unreachable).
      `EnvironmentFile=-/etc/default/gps-dahua` added to `gps-dashboard.service` so the proxy
      has the password. Pillow added as a runtime dep. 7 route tests.
- [x] **Client:** grid polls the snapshot proxy on a shared cache-bust stamp
      (`SNAPSHOT_REFRESH_MS` 5 s, gated on visibility + paused while live); tap a tile →
      live 720p (`-hd`) WHEP video via the Phase 2 client, wake-lock held while watching.
      Pure bits in `lib/cameras.ts` (+ test). Typecheck + 163 tests + build green.
- [x] Deployed + verified on-device (browser-driven): all four thumbnails load (~13–23 kB
      at 480×271, self-scheduling refresh — an initial shared-timer version starved the
      slowest tile; per-tile self-scheduling fixed it), tap → live plays **1280×720** WHEP
      video (`readyState` 4), close tears down cleanly, 0 JS errors (a transient rear-camera
      snapshot 503 is retried gracefully). Snapshot proxy returns a real downscaled JPEG.

**Deferred to Phase 4 / polish:** a busy camera occasionally 503s a snapshot (it's serving
the NVR main stream); the retry recovers it, but a short server-side last-good cache would
smooth the grid. Driving-mode multiview + glass-to-glass latency vs the NVR HDMI still open.

## Phase 4 — Expand + driving mode

- [ ] Tap a tile → expand to the 720p third stream (`subtype=2`).
- [ ] **Driving mode:** pre-warmed multiview (blind-left + blind-right + rear, maybe front),
      short-GOP 720p, latency-first. Decide placement — its own Cameras sub-view vs adjacent
      to the Drive view. Validate glass-to-glass latency vs the NVR HDMI feed.

## Streams available through the hub (reference)

Every path is on-demand (idle cost zero). Per camera `<pos>` ∈ {front, blind-left,
blind-right, rear}:

| Path | Codec / res | Consumer |
|---|---|---|
| `cam-<pos>` | H.264 D1 704×480 | WebRTC (grid tile) + RTSP |
| `cam-<pos>-hd` | H.264 720p | WebRTC (tap-to-live) + RTSP |
| `cam-<pos>-main` | **H.265 2688×1520 + audio** (front PCM / rear G.711; blind cams none) | **RTSP only** (OBS/VLC — WHEP can't decode H.265) |

- RTSP: `rtsp://192.168.42.178:8554/<path>` · WebRTC page: `http://192.168.42.178:8889/<path>`
  (H.264 paths only). OBS: **Media Source** → uncheck Local File → paste the RTSP URL →
  add input option `rtsp_transport=tcp`.

## Open items / notes

- A bigger NVR HDD is the real lever if more than ~2–4 days of recording retention is ever
  wanted — orthogonal to this streaming work (main streams untouched here).
- Audio: only front (PCM, not WebRTC-friendly) and rear (G.711 a-law, WebRTC-compatible)
  have mics, and only on the main (H.265) stream. Skipped for now; revisit if a driving/
  rear feed wants sound (would need audio enabled on a sub/third stream).
- The `cam1`–`cam4` paths already in `mediamtx.yml` are the hill-climb SRT *publisher*
  paths (`plans/streaming-platform-plan.md`) — different mechanism; don't reuse those names.
