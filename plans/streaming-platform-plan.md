# Live Video Streaming Platform Plan (Hill Climb)

> Living plan. **Greenfield as of 2026-07-20.** Reuses the MediaMTX hub built for
> the radio (**R10**, `plans/radio-platform-plan.md`) as the single aggregation
> point — this adds camera ingest, a Pi-side edge encoder, and OBS production on
> top of it. The **network/RF/IP** side (5 GHz PtP backhaul, camera-Pi IP plan,
> egress) is documented in `paul-network-docs` → `events/2026-hillclimb.md`.

## Context

For the annual Vermont hill climb, pull **3–4 live camera feeds + the radio** into
**OBS on the operator laptop** and stream to YouTube. The van parks at the top of
the course with a wired venue uplink; each camera is a **Raspberry Pi + USB cam** at
a position downhill, backhauled over a **dedicated 5 GHz PtP** link onto the van LAN.

The hub already exists (radio R10). New work is three things: an edge encoder on each
camera Pi, MediaMTX ingest config, and OBS production. Everything pulls from the one
hub — OBS attaches to MediaMTX for all feeds *and* `/radio`, never to the publishers.

## Decisions

- **S1 — One hub, reuse the radio's MediaMTX.** Add camera `paths:` to the existing
  `mediamtx` service; do **not** stand up a second server. OBS pulls `cam1`–`cam4`
  + `radio` from the one hub on [`pmpi1`](https://github.com/pmormr/gps-dashboard).
  This is exactly the "hub later proxies the cams for OBS" future named in R10 / 2f-e.
- **S2 — Encode at the edge, transport SRT.** Each Pi runs one ffmpeg job: USB cam
  (V4L2) → H.264 → **SRT publish** to MediaMTX (streamid `publish:camN`). SRT over
  raw RTSP/UDP for retransmit (ARQ) + a tunable latency buffer, so a lossy link
  degrades gracefully. (Chose SRT over RTMP-push: RTMP is TCP-only → head-of-line
  blocking on a flapping link. RTSP-push works but SRT's latency knob is the point.)
- **S3 — The Pi encoder is a minimal purpose-built unit, not this stack.** Just
  ffmpeg + a systemd service + env config (cam device, resolution, bitrate, hub
  target) — it does not carry gps-dashboard. **Pi 4 preferred** (hardware H.264 via
  `h264_v4l2m2m`, near-zero CPU); **Pi 5 has no HW H.264 encoder** → software
  `libx264 -preset veryfast` (fine at 720p, watch CPU at 1080p). USB-cam capability
  varies — many do MJPEG/YUYV only, so ffmpeg transcodes; some UVC cams emit H.264
  natively (passthrough, cheapest). Enumerate with `v4l2-ctl --list-formats-ext`.
  **First unit — picam1 (Pi 4B, 2× LifeCam HD-3000):** the HD-3000 is **YUYV-only**
  (no MJPEG/H.264), so 720p caps at 10 fps and 30 fps needs 480p/360p; encode is the
  Pi's HW `bcm2835-codec-encode` (`/dev/video11` → `h264_v4l2m2m`). An MJPEG/H.264 UVC
  cam is the upgrade lever for framerate + USB headroom. See `paul-network-docs`
  `van/devices/picam1.md`.
- **S4 — Bitrate budget.** ~2–3 Mbps per feed at 720p30/1080p30 H.264 — a fraction
  of a 5 GHz PtP link, so backhaul is not the constraint. OBS mixes to a **single**
  ~6 Mbps 1080p output to YouTube.
- **S5 — Egress.** OBS streams over the venue **wired** uplink (primary) via
  `van-edge`; Starlink is failover. YouTube Live RTMP. (Details + `mwan3` note in the
  network-docs event page.)

## Phase 0 — MediaMTX ingest enablement

- [ ] `deploy/mediamtx.yml`: set **`srt: true`** (currently off) and add `paths:`
      `cam1`–`cam4` (`source: publisher`). Keep `radio` as-is. Deploy hook already
      restarts `mediamtx` on config change — ships on push, no manual `systemctl`.
- [ ] Verify a test SRT publish (ffmpeg from the laptop) shows up and pulls back via
      RTSP (`rtsp://pmpi1:8554/cam1`) and WebRTC (`http://pmpi1:8889/cam1`).

## Phase 1 — Edge encoder (per-Pi)

- [ ] Bring up one Pi + USB cam on the bench: enumerate formats, pick the cheapest
      encode path (native H.264 passthrough → `h264_v4l2m2m` → `libx264`).
- [ ] ffmpeg → SRT publish to the hub, with reconnect/backoff (link flap is expected).
- [ ] systemd unit + env config (device, WxH, fps, bitrate, hub host/port, streamid).
      **Open:** do these Pis deploy via a bare-repo hook like `pmpi1`, or stay
      standalone (scp'd unit)? They're separate hosts, off the gps-dashboard deploy
      path — leaning standalone + a tiny provisioning script. Decide before Phase 3.

## Phase 2 — OBS production

- [ ] Media source per path (`cam1`–`cam4`) + `radio` as an audio source (already
      live). Scenes per position + a PIP/multiview. Test switching.
- [ ] Record-to-disk pass first, then stream to YouTube. Tune latency (SRT `latency`
      vs. OBS buffer) — trade glass-to-viewer delay against stall resistance.

## Phase 3 — Field hardening

- [ ] Reconnection under real link flap (pull a PtP link, confirm SRT re-establishes
      and OBS recovers the source).
- [ ] Egress failover (`mwan3` on `van-edge`, wired→Starlink) — optional.
- [ ] Full dry run at the base before event day (all 3–4 feeds + radio + YouTube).

## Open items / purchases

- 5 GHz PtP radios + PoE + mounts, USB cams, Pi units, per-position power — tracked on
  the network-docs field checklist (`events/2026-hillclimb.md`).
