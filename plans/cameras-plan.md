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

## Operational traps (learned during Phase 0)

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

## Phase 1 — Hub (MediaMTX paths + secret) — NEXT

- [ ] `deploy/mediamtx.yml`: add on-demand pull paths for the four cams. Sub for glance;
      720p for expand/driving. Sketch (verify exact on-demand/transport keys against the
      pinned **v1.19.2** schema — likely `sourceOnDemand`, `sourceOnDemandCloseAfter`,
      `rtspTransport`):
      ```yaml
      cam-front:
        source: rtsp://admin:${GPS_DAHUA_PASSWORD}@192.168.42.51:554/cam/realmonitor?channel=1&subtype=1
        sourceOnDemand: yes
        rtspTransport: tcp
      cam-front-hd:
        source: rtsp://admin:${GPS_DAHUA_PASSWORD}@192.168.42.51:554/cam/realmonitor?channel=1&subtype=2
        sourceOnDemand: yes
        rtspTransport: tcp
      # …repeat for blind-left .52 / blind-right .53 / rear .54 (subtype 1 + 2 each)
      ```
- [ ] `deploy/mediamtx.service`: add `EnvironmentFile=-/etc/default/gps-dahua` so the
      `${GPS_DAHUA_PASSWORD}` interpolation resolves.
- [ ] Push → hook restarts `mediamtx`. Verify pull-back from the LAN:
      `rtsp://pmpi1:8554/cam-front` (VLC/ffprobe) and browser WHEP at
      `http://pmpi1:8889/cam-front`. Confirm on-demand: the pull opens on connect, closes
      after idle (watch the mediamtx journal).
- [ ] Confirm `mediamtx.service` is enabled (radio 2f already runs it; a config-only
      change just needs the restart).

## Phase 2 — Video WHEP client

- [ ] Generalize `web/src/lib/radioListen.ts` (audio-only) into a shared WHEP client that
      adds a `video` transceiver and returns a stream for a `<video>`. Keep the bare
      `fetch` + `RTCPeerConnection`, host-ICE-only, no-STUN approach. Factor a common
      `whep.ts`; radio keeps audio-only, cameras use video.

## Phase 3 — Cameras tab (glance-first)

- [ ] New top-level **Cameras** tab (`SECTIONS`/`routes.ts`/`Shell`, Radio-style), 2×2 grid.
- [ ] **Design fork — grid rendering:**
  - (a) **Live sub streams on-demand** — simplest, one code path; but ~4 continuous
    streams while the tab is open (~2 Mbps over HaLow from home). Ship as the MVP to prove
    camera → hub → browser end-to-end.
  - (b) **JPEG snapshot thumbnails + tap-to-live** — near-zero idle, HaLow-friendly; needs
    a small Flask snapshot-proxy route (`GET /api/cameras/<node>/snapshot`, server-side
    digest fetch → JPEG, creds stay server-side). Add after (a) for bandwidth.
- [ ] Cameras registry for the API/frontend (node → host, stream URLs). Reuse/adapt
      `FLEET` from `sensors/dahua_reader.py` rather than duplicating IPs.

## Phase 4 — Expand + driving mode

- [ ] Tap a tile → expand to the 720p third stream (`subtype=2`).
- [ ] **Driving mode:** pre-warmed multiview (blind-left + blind-right + rear, maybe front),
      short-GOP 720p, latency-first. Decide placement — its own Cameras sub-view vs adjacent
      to the Drive view. Validate glass-to-glass latency vs the NVR HDMI feed.

## Open items / notes

- A bigger NVR HDD is the real lever if more than ~2–4 days of recording retention is ever
  wanted — orthogonal to this streaming work (main streams untouched here).
- Audio: only front (PCM, not WebRTC-friendly) and rear (G.711 a-law, WebRTC-compatible)
  have mics, and only on the main (H.265) stream. Skipped for now; revisit if a driving/
  rear feed wants sound (would need audio enabled on a sub/third stream).
- The `cam1`–`cam4` paths already in `mediamtx.yml` are the hill-climb SRT *publisher*
  paths (`plans/streaming-platform-plan.md`) — different mechanism; don't reuse those names.
