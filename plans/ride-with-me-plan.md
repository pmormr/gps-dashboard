# Ride With Me — livestreamed drives Plan

> Living plan. **Started 2026-08-24.** A YouTube livestream show (working name
> "ride with me"): casual POV drives between cities/places — e.g. the Blue Ridge
> Parkway, St. Louis → DC. Format is **ambient slow-TV** (scenic POV + map overlay
> + light narration), with chat-interactive episodes possible later (never solo —
> a co-pilot works chat). Reuses the broadcast tier's two hubs + registry
> (`.claude/modules/broadcast.md`); the genuinely new piece is an unattended
> **cloud-side switcher** that owns the YouTube session while the van's Starlink
> link comes and goes.

## Context

The van has **Starlink Mini** WAN (officially in-motion capable; CGNAT — the van
always dials out). In motion under tree cover the link will flap constantly:
micro-drops of seconds, real outages of minutes. The architecture must absorb
both without ending the YouTube broadcast, and must run with **nobody at a
production console** — the only human is driving.

Three buffer layers, one per outage scale:

1. **SRT latency buffer** (~8–12 s) on the van→cloud publish — rides micro-drops.
2. **Slate switch on the vps** — the switcher feeds YouTube a "signal lost — back
   shortly" loop while the ingest path has no live source, and cuts back when the
   van reappears. Rides real outages.
3. **YouTube DVR** — viewers scrub back over whatever survived.

What already exists and is load-bearing here: the authed cloud MediaMTX on
`vps202051` (van→cloud publishing proven by the drone-RTMP work), the
`broadcast/feeds.py` registry + Broadcast tab wall (two-sides status of exactly
this kind of feed), the WG control tunnel (B7), and `cam-front` — a forward
exterior 4MP Dahua already serving an H.264 720p30 ~2 Mbps third stream
(`cam-front-hd`) through the van hub. Phase 0 needs **zero new hardware**.

Distinct from: `cameras-plan.md` (LAN viewing of the same cams) and the
hillclimb event flow (OBS + a human operator at home). Same hubs, different
production model: here production is unattended and lives on the vps.

## Decisions

- **RW1 — The vps owns the YouTube session.** One continuous RTMP(S) push to
  YouTube originates on `vps202051` and never depends on the van link. The van
  (or the laptop) *feeds* it when connected. No source ever publishes to YouTube
  directly — that invariant is what makes come-and-go possible.
- **RW2 — Van is a dumb, resilient source; all production is cloud-side.** The
  van publishes one SRT feed to the cloud hub with a generous latency buffer and
  otherwise does nothing (no slate, no overlay, no mixing van-side). The laptop
  running OBS in the van is just an alternate *source* publishing to the same
  hub path — richer composition, same invariant.
- **RW3 — The slate is the switcher's own looped media, not MediaMTX.**
  `alwaysAvailable` serves **no frames** (the broadcast module's event-day
  correction) — it cannot be the filler. The switcher carries its own slate
  file/loop and its own silence bed.
- **RW4 — v1 switcher = ffmpeg supervisor with restart-on-transition; constant
  output shape.** A small agent on the vps (the `cloud_agent.py` pattern:
  repo-tested code, off-repo manual install) pushes to YouTube, watching the
  ingest path via the control API (`source.id` non-empty — the B6
  discriminator) and/or its own reader stalling. On transition it restarts the
  ffmpeg push with the other input; YouTube tolerates a same-key reconnect as a
  few seconds of freeze. Output is **always re-encoded to one fixed shape**
  (720p30 x264 + AAC — silent `anullsrc` until there's real audio) so live and
  slate segments are interchangeable and YouTube never sees param changes.
  Design toward the v2 compositor (Phase 2); don't start there.
- **RW5 — Ride feeds live in the existing registry + hub.** `broadcast/feeds.py`
  gets the ride entries (cloud hub, authed publish — the scoped-user pattern),
  so the Broadcast tab's wall/config/status cover the show for free. The cloud
  `mediamtx.yml` stays hand-maintained (B8 renders van paths only).
- **RW6 — The cloud hub keeps its torn-down-between-events posture.** A drive is
  a mini-event: stand up MediaMTX + UFW before, tear down after (the scanner
  noise that motivated teardown hasn't changed). Friction is handled by
  scripting the up/down runbook, not by leaving public ports open.
- **RW7 — Ambient-first; interactivity must not fork the architecture.** Chat
  episodes later differ only per-episode: YouTube latency setting, a smaller SRT
  buffer, and a co-pilot on chat. Nothing structural.
- **RW8 — No copyrighted audio can reach the program path.** Cab radio/Spotify
  on a live mic = Content ID mutes/strikes on the VOD. Road ambience +
  narration are the show; any music bed is injected cloud-side from
  licensing-safe material.
- **RW9 — The YouTube stream key lives on the vps** (`/etc/default/` root-600,
  the cloud-agent secret pattern). This revisits the broadcast plan's dropped
  "stream key slot" with a changed premise: the key is no longer an OBS field a
  human pastes per-broadcast — it's set-once config for an automated publisher
  (YouTube persistent keys are stable until rotated).

## Phase 0 — Driveway/around-town proof (zero new hardware)

Goal: end-to-end chain live on an **unlisted** stream; learn how the Mini
behaves in motion and whether `cam-front` is watchable as a show source.

- [ ] Stand up the cloud hub (RW6 runbook: `systemctl enable --now mediamtx` +
      UFW rules per `cloud/devices/vps202051.md`); add a `ride-pov` path with a
      scoped publish cred (hand-edit, per RW5).
- [ ] Ad-hoc van relay: ffmpeg on the Pi pulling `rtsp://127.0.0.1:8554/cam-front-hd`
      (H.264 **passthrough** — no Pi transcode) → SRT publish to `ride-pov`,
      `latency` ~8–12 s. No unit yet; a shell invocation is fine for the proof.
- [ ] Switcher v0 on the vps: supervised ffmpeg per RW4 — live input when the
      path has a real source, slate loop otherwise, fixed 720p30 + silent AAC
      out to an unlisted YouTube stream (persistent key, DVR on).
- [ ] Make a slate: a static "ride with me — signal lost, back shortly" card
      (loop or looped short clip) + silence.
- [ ] Drive a wooded/hilly local loop for ≥1 h. Capture: slate transition count
      + durations, SRT retransmit stats, data consumed (vs the plan's terms),
      YouTube ingest health, and a subjective watchability call on `cam-front`
      (framing, exposure into sun, vibration).

## Phase 1 — Productize the v1 pipeline

- [ ] `broadcast/feeds.py`: `ride-pov` entry (cloud hub) so the wall/config
      surfaces it; codec pin per B6's gating rules.
- [ ] Van relay → enabled-gated unit (`ride-relay.service`, the sensor-reader
      gating pattern). Trap: the post-receive hook needs its per-unit restart
      block added on the Pi for any brand-new service.
- [ ] Switcher → `broadcast/ride_switcher.py` + an off-repo unit on the vps
      (kept OUT of `deploy/`, like `cloud_agent.service`); pure switch logic
      table-tested in `tests/`.
- [ ] Script the RW6 stand-up/tear-down (vps-side helper the runbook calls).
- [ ] YouTube channel setup: name/branding (working title pending), persistent
      key into the vps env file (RW9), default = unlisted until the show is real.
- [ ] First real episode on a scenic route; review the VOD + slate behavior.

## Phase 2 — The show layer (compositor, overlay, audio)

- [ ] **Compositor v2**: headless OBS (or equivalent) on the vps replaces the
      bare ffmpeg switch — browser-source overlay, smooth slate transitions,
      music bed. Verify how it ingests first: the B13 RTSP-into-OBS trap was
      learned over the LAN; confirm whether localhost RTSP on the vps stalls
      the same way before designing around WebRTC/HLS.
- [ ] **GPS overlay** (the differentiator): live map + speed/elevation/route
      progress as a browser overlay off our own stack — the
      `hillclimb-timing-overlay` pattern pointed at our own data. Needs a
      position feed reaching the vps: van POSTs over the WG tunnel (agent
      endpoint or a switcher endpoint) vs. MQTT — decide here. Converges with
      the parked OwnTracks/phone-tracking idea.
- [ ] **Audio**: pick the path — enable AAC on `cam-front`'s third stream
      (Dahua config; mic is currently PCM-on-main only), a cab/road mic via the
      laptop source, or a dedicated mic node. RW8 governs content either way.
      (The Digirig is radio-plane hardware — not a candidate; RTS=PTT.)
- [ ] **Camera**: decide whether `cam-front` carries the show or episode-grade
      needs a dedicated POV cam (hillclimb-cam node publishing SRT direct to
      the cloud hub) — informed by Phase 0's watchability call.

## Phase 3 — Interactive episodes (deferred until the show has legs)

- [ ] Co-pilot chat workflow (phone/laptop, nothing pipeline-side).
- [ ] Per-episode low-latency profile: YouTube latency setting + a smaller SRT
      buffer; measure what glass-to-glass it actually yields through the relay.
- [ ] Optional: chat/POI callouts composited into the overlay.

## Open items / notes

- **Starlink Mini plan terms** — which Roam tier; data budget. At ~2–3 Mbps the
  passthrough feed is ~1–1.4 GB/h; a long drive day ≈ 10 GB.
- **vps CPU headroom** — RW4 re-encodes 720p30 x264 continuously for hours;
  check `vps202051`'s cores/steal before Phase 1 commits to always-transcode
  (fallback: passthrough-when-live + slate matched to the cam's exact params,
  fragile but cheap).
- **12 h VOD cap** — YouTube archives cap at 12 h/stream; a marathon route needs
  planned re-starts (natural at fuel/food stops).
- **Overlay position transport** — decided in Phase 2 (agent endpoint vs MQTT).
- **Laptop-as-source ergonomics** — OBS scene kit for the van laptop (cabin cam
  PiP, mic) once it's ever the source; publishes to `ride-pov` like any source
  (RW2).

## Related

`.claude/modules/broadcast.md` (hubs, registry, B6/B7/B13, the `alwaysAvailable`
correction RW3 rests on) · `plans/cameras-plan.md` (`cam-front-hd` stream shape,
C3) · sibling repos `hillclimb-cam` (POV-cam node option) and
`hillclimb-timing-overlay` (the overlay pattern).
