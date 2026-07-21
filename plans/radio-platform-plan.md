# Radio Control Platform Plan (Icom ID-5100A)

> Living plan. The control + recording + live-listen planes are **built and
> deployed**; this file now tracks the open work.
>
> **Status (2026-07-18):** Phases 0–2f landed 2026-06-24 → 2026-07-18. The durable
> architecture, traps, and operational detail live in **CLAUDE.md's "Radio Control
> (CI-V)" section**, the `radio/` package, and git history. The **Decisions** block
> below is kept verbatim — code comments anchor to its R-numbers (`R2`/`R6`/`R7`/`R8`/
> `R9`/`R10`) — so it is the load-bearing reference, not history. Everything below the
> Decisions is the remaining open work: Phase 3 (announcements/TX) + polish.

## Context

Control the van's **Icom ID-5100A** dual-band (2 m/70 cm, FM + D-STAR) transceiver
from the Pi, and — later — log received transmissions (GPS-joined, map-pinnable) and
play scheduled announcements. The radio is a peer to the existing telemetry streams;
this reuses the project's shape (standalone Pi-side service + Flask routes + a page,
GPS-joined like OBD/Victron).

**The interface splits into two physically separate planes:**

- **Control plane — CI-V serial.** A single-wire half-duplex TTL bus. This is what
  Phase 0/1 cover. Hardware is in hand and proven.
- **Audio plane — RX record + TX announce.** Travels over *different* jacks (RX = the
  speaker jack, TX = the mic jack) and needs a *separate* USB audio interface + a PTT
  method. Phases 2–3. No hardware yet.

The clone cable touches only the control plane.

## Decisions

- **R1 — Two planes, control-first.** Build control (CI-V) first; it's de-risked and
  needs no new hardware. Recording and announcements are the audio plane, gated on a
  USB sound interface.
- **R2 — Hamlib `rigctld` is the CI-V layer.** Model **3071** (Icom ID-5100). A
  long-lived `rigctld` daemon owns the serial port and exposes the simple TCP text
  protocol on `127.0.0.1:4532`; the Flask app speaks that protocol with a stdlib
  socket — **no** Python Hamlib binding. Rationale: free, battle-tested command set
  (freq/mode/memory/S-meter/PTT) and the daemon-owns-the-port model cleanly solves
  serial contention. `libhamlib-utils` is an apt system dep installed on the Pi while
  online — acceptable because the offline constraint is about *runtime* working
  off-grid, not avoiding cacheable dev-time deps. (Chose this over a pure-Python CI-V
  module built on `civ_probe.py`.)
- **R3 — udev-pinned, enabled-gated daemon.** A udev rule pins the cable
  (VID:PID `1a86:55d3`) to a stable `/dev/icom-civ` symlink (the GPS dongle does the
  same). The `radio-control` service stays **disabled** until the cable is wired, like
  `sensor-obd`/`sensor-victron`, so a host without the radio never crash-loops.
- **R6 — Phase 1 scope (decided 2026-06-24, post-1a).** **Main band only** (get-VFO
  unsupported makes a live dual-band readout flip the radio's active VFO each poll).
  **Core + tone/repeater:** freq/mode/S-meter readout + set-freq/set-mode, plus
  CTCSS/DCS tone set and repeater duplex/offset set (all backend-confirmed in 1a).
  **No** memory recall (backend exposes none). Service named **`radio-control`** (not
  the gps- family; it's a transceiver, not GPS).
- **R4 — recording data model (Phase 2 sketch).** `radio_transmissions(id, started_utc,
  ended_utc, freq_hz, mode, duration_s, audio_path, lat, lon, ...)` — lat/lon snapped
  from the live GPS stream at capture time, fully GPS-joinable; map pins reuse the 🚁
  drone-overlay path. Audio files on the NVMe, DB row is metadata.
- **R8 — recorder design (2c walk-through, 2026-07-18).** **Audio-VOX is the
  gate** — RMS on the SP1 stream itself, so the trigger hears exactly what gets
  recorded (including sub-band traffic in the A+B mix); DCD + freq/mode are polled
  once at gate-open as *metadata only*, so the pegged-RAWSTR/DCD-false anomaly can
  never drop a recording. Details: continuous `arecord` (native mono S16_LE 48 kHz
  from `plughw:CARD=Digirig`) piped into the recorder, ~3 s ring-buffer pre-roll,
  VOX on ~100 ms RMS blocks (open ≈ −40 dBFS against the measured −49 floor /
  −28 open-squelch static, ~2 s hang, hard per-file cap so a stuck carrier can't
  grow an unbounded file). Storage: stdlib-`wave` WAV under
  `/mnt/nvme/data/radio-audio/YYYY-MM/` (dir derives beside the DB, env-overridable),
  age+size-cap pruner that NULLs `audio_path` but keeps rows — the log outlives the
  audio; deliberately outside the backup path like tiles. Schema drops R4's
  sketched band column: get-VFO is unsupported, so which band is Main is
  *unreadable* — freq/mode are recorded as the active-main-band readout and
  `dcd_main` marks the tag's confidence (sub-band audio may carry a wrong freq
  tag). GPS snap = latest raw fix, direct DB read, NULL when stale (>5 min).
  Service `radio-recorder`, enabled-gated; pins C-Media mixer state (AGC off,
  gain) and AF via rigctld at startup (the 2a replug-resets-mixer trap).
- **R9 — commit rule: activity separates, level cannot (2e, 2026-07-18).**
  Corpus analysis of the first live day (block-level RMS + zero-crossing over
  real captures) showed false triggers — squelch crackle, rig beeps — are
  single 100–500 ms transients at *exactly voice level* (loud-block median
  −30.3 dBFS vs voice −30.2), so no OPEN-threshold raise can separate them.
  What separates is total above-threshold activity per capture: blips measured
  1–5 loud blocks, voice 10–12. The gate commits a capture on close only when
  it accumulated ≥ `GPS_RADIO_MIN_LOUD_BLOCKS` (default 6 ≈ 600 ms) blocks at
  the open threshold, else discards; pending captures buffer in RAM, so a
  discard never touches disk or DB. DCD is polled ~1 Hz across active captures
  (any open reading → `dcd_main=1`), fixing the gate-open-beats-the-squelch
  miss. Accepted trade: an isolated one-word transmission (~3–4 blocks)
  discards too — K is the knob if that ever stings. (ZCR turned out to be a
  weak discriminator — the rig's speaker path band-limits even static; noted
  so nobody re-derives it. FM-quieting detection stays in the pocket as v2.)
- **R10 — live listen / streaming architecture (2f, 2026-07-18).** **MediaMTX is
  the media hub** (single static arm64 binary on the NVMe — offline-safe once
  installed, same reading as Hamlib) fed by an enabled-gated ffmpeg publisher
  (`radio-stream`): shared Digirig capture → Opus 48 kHz mono ~48 kbps → RTSP
  publish on localhost. Listeners attach to the hub, never the publisher: VLC
  `rtsp://<pi>:8554/radio` (~0.5–1 s), the built-in WebRTC page
  `http://<pi>:8889/radio` (sub-second — the level-tuning path), OBS media
  source. **Multicast rejected**: WiFi multicast is sent at base rate with no
  retransmit (power-saving phones miss frames wholesale) and dies at the HaLow
  bridge; the camera plan it would "mirror" is really RTSP unicast (Dahua) — the
  thing to mirror is the *hub*, which later proxies the cams for OBS via
  `paths:` entries. **Capture sharing = ALSA dsnoop** (`/etc/asound.conf`,
  manual install like the udev rules): the hw device is single-open, dsnoop
  lets recorder + publisher capture independently — so listening works while
  `radio-recorder` is stopped or being reconfigured, exactly the tuning
  scenario. The slave pins the recorder's native format (S16_LE 48 k mono) with
  a 2 s ring so the recorder's overrun margin survives. (Chose the hub over an
  in-app chunked-Ogg Flask route — no camera future, 1–3 s latency — and over
  Icecast — highest latency, serves neither goal. Chose dsnoop over a
  recorder-side FIFO tee — the tee couples streaming to the recorder's
  lifecycle and grows the one daemon that must never break.) `/radio`
  Listen-live embed (WHEP) deferred to polish.
- **R5 — announcements are Part-97 gated (Phase 3).** Automated TX carries FCC
  obligations: station ID (§97.119), a control operator, automatic-control limits, no
  broadcasting/music. Designed in (call-sign ID injection + a sane trigger model), not
  bolted on. Recording your own RX is unencumbered.
- **R11 — transmit console: operator-clicked, not automated (Phase 3, 2026-07-20).**
  TX is **attended remote control only** — every transmission is a `/radio` button
  press by the licensed control op (**KC3HEU**); no scheduler/beacon, which sidesteps
  §97.109 automatic-control limits and §97.113(b) broadcasting entirely (the two live
  Part-97 hazards R5 flagged). Two input methods: a **filesystem-driven soundboard**
  (WAVs dropped under the audio root, listed + click-to-send) and **dual switchable
  TTS** — espeak-ng default (the offline Microsoft-Sam-family formant synth) with
  piper the natural-voice alternate; both are manual/system installs like Hamlib
  (espeak-ng = apt, piper = a committed NVMe model), not carried by `uv sync`.
  **PTT = CI-V** (`set_ptt` via rigctld), chosen over the Digirig RTS line so the
  Digirig serial port and its RTS=PTT guard stack stay untouched; a `try/finally`
  release + a hard max-duration watchdog enforce the **never-stuck-keyed invariant**
  (a wedged transmitter is the worst failure mode — illegal, jams the freq, cooks the
  finals). **Self-TX is logged from the clean source audio** (the exact rendered/
  played bytes, not the SP1 loopback): one `radio_transmissions` row tagged
  TX-direction so the log unifies RX + TX, and a TX-active sentinel suppresses the
  recorder so RX never double-logs the same transmission. **Callsign ID is
  operator-manual** — baked into the message text / clips, never auto-injected
  (KC3HEU's call; the console offers no backstop by design). **RF-ingress gate:** the
  2a TX crashed the Digirig USB (−71), so first on-air tests are low-power with a TX
  audio-drive (deviation) calibration before the path goes hot. (Chose a Flask route
  that shells out over a `radio-announce` daemon for v1 — button-press cadence, short
  transmissions; promote to a daemon only if codec contention forces it.)

## Phase 0 — Feasibility — DONE (2026-06-24)

CI-V control plane proven on real hardware: OPC-478UC clone = **WCH CH343**
(`1a86:55d3`), single-wire CI-V on the **[SP2]** jack, **19200 baud / address 0x8C**
(`tools/civ_probe.py` read the live VFO). Detail in git history.

## Phase 1 — Control (CI-V) — DONE + deployed (2026-06-24 → 07-02)

Main-band freq/mode/S-meter (RAWSTR) readout + set, CTCSS/DCS tone, and repeater
duplex/offset — live via the `radio-control` rigctld service (model 3071,
`/dev/icom-civ`), `api/routes/radio.py` + `api/rigctld.py`, and
`web/src/views/Radio.svelte`. Scope + the backend capability map = **R6** and **Q2**
(memory recall + get-VFO unsupported → main-band only; `-C civaddr=0x8C` proved
redundant on model 3071). Deploy-hook enabled-gated restart stanza live. Play-by-play
in git history.

## Phase 1.5 — Control-plane enrichment (CI-V only) — DONE except 1.5e

Rides the existing rigctld daemon (**R7**: raw CI-V goes *through* rigctld via
`send_cmd`, never a second serial client). Landed: calibrated S-meter (RAWSTR
`0000`=S0 / `0170`=S9), AF/SQL/RFPOWER control (`POST /api/radio/level`),
deterministic band-select (raw CI-V `07 D0/D1`), and dualwatch (`16 59`; reads use
rigctld's *non-extended* `send_cmd_rx`). The 2e noise-storm diagnosis traced chatter
to a **marginal main-band squelch (0.165) → raised to 0.25** — squelch level is the
first knob to check if chatter returns. D-STAR heard-log dropped (Paul doesn't use it).

- [ ] **1.5e — cross-band repeater support (PROPOSED).** Repeater Mode itself is
      **not CI-V-controllable** — the manual's command table has no enter/exit
      command (no `1A` extended family on this rig), community sources show no
      undocumented one, and blind write-fuzzing the rig is poor risk/reward. Scope
      is everything *around* the USA-only mode instead: **(1)** a one-tap "stage
      cross-band" action that sets both bands' freq/mode/tone (band-pin + sets),
      dualwatch ON (raw CI-V `16 59 01` — a required precondition), and TX power,
      leaving only the touchscreen confirm; **(2)** live-validate whether the rig
      accepts CI-V at all inside Repeater Mode (front panel locks to [MONI]);
      **(3)** validated CI-V power off/on (`18`, wakeup preamble before `18 01`) —
      Repeater Mode survives power-off, enabling remote power-cycling. Part-97 note:
      cross-band retransmission has station-ID obligations the 5100 doesn't
      automate; operator's responsibility, outside the app.

## Phase 2 — Transmission recording — DONE except the field/purchase items

Digirig Mobile + Icom RJ-45 kit. VOX-gated recorder (`radio/` package,
`radio-recorder` service) — **R8** design (audio-VOX primary, DCD-as-metadata,
ring-buffer pre-roll, native-format WAV + pruner) + **R9** commit rule (a capture
commits only with ≥ `GPS_RADIO_MIN_LOUD_BLOCKS` of activity — blips sit at voice
level, so activity separates them, thresholds can't). `radio_transmissions` schema +
the `/radio` transmission log with in-browser playback (2d; map overlay dropped —
rows keep lat/lon if it ever revives). Deployed + verified 2026-07-18. **Durable
wiring facts (they inform the open items below):**

- RX tap = **[SP1]** (SP2 is the CI-V port); plugging SP1 mutes the internal speaker
  → Y-split one leg to a cabin speaker, one (through a 10–20 dB pad) to the Digirig.
- TX audio = **mic pin 6**, PTT = **pin 4** (RJ-45 leg); CI-V PTT stays primary.
- **Ground-loop hum watch** — Pi + radio share van 12 V ground.
- **RTS = PTT trap** (the 2a dead-key incident: RTS assert keys TX): the guard stack
  (ModemManager masked, gpsd `USBAUTO=false`, `deploy/99-digirig.rules`,
  `digirig-rts-clear` oneshot) is non-negotiable — see CLAUDE.md.

- [ ] **2c-tail — needs Paul at the rig / in the field.** (1) Reboot test
      **watching the rig**: the clearer's open() blips RTS for ~ms (kernel
      behavior, accepted) — confirm it doesn't meaningfully key TX and that
      post-boot RTS reads clear (`journalctl -u digirig-rts-clear -b`). (2) The
      RAWSTR-pegged/DCD-false anomaly did **not** reproduce (RAWSTR 17 + DCD false =
      sane; DCD read 1 when open) — keep an eye out, but both reads look healthy.
      `dcd_main` is now polled across the capture (R9), so it's a much stronger hint.
- [ ] **Purchase:** 3.5 mm Y-splitter (restores the cabin speaker SP1 muted) +
      the 10–20 dB pad (decouples cabin listening volume from record level —
      without it, cranking AF for the speaker also cranks the Digirig leg;
      re-run the AF calibration when it lands). **Isolator decision — RESOLVED
      2026-07-21** (measured with `tools/radio_floor.py`): the ~−50 dBFS floor was
      USB-domain digital contamination — a 1 kHz USB-SOF family + a 180 Hz–1 kHz
      hump sitting *in the voice band*. A **USB ground isolator** cuts it ~13 dB
      (floor → ~−64, static/signal unchanged ⇒ real SNR, not attenuation, and
      electrically transparent); the audio isolator gave only ~4 dB alone and ~1 dB
      on top of the USB one (redundant, plus a transformer in the voice path). **USB
      isolator adopted + inline; audio isolator shelved.** VOX thresholds lowered
      −40/−45 → −52/−56 to exploit the new floor (~12 dB weaker signals now trip
      the gate). Engine-running / alternator-whine re-check still open.
- [ ] **Flagged (discuss before acting): CI-V-via-Digirig consolidation.** The
      Digirig serial port could replace the CH343 CI-V adapter (TX/RX bridged =
      same single-wire topology), freeing a USB port. Needs a separate CI-V
      cable for the Digirig serial jack, explicit `rts_state`/`dtr_state` OFF in
      rigctld (RTS = PTT on this port!), and live validation. Current CH343 path
      is deployed and proven — no urgency.

## Phase 2f — Live listen / network audio stream (R10) — DONE except 2f-e

MediaMTX hub (`mediamtx` service, static binary + `deploy/mediamtx.yml`) + ALSA
dsnoop shared capture (`deploy/asound.conf`) + ffmpeg Opus publisher
(`radio-stream`) → `rtsp://<pi>:8554/radio` + `http://<pi>:8889/radio` (WebRTC).
Deployed 2026-07-18. AF recalibrated 0.15 → 0.25 via live listening; the ~−50 dBFS
floor was input-referred USB-domain contamination — a **USB ground isolator** (added
2026-07-21) cuts it ~13 dB to ~−64 with the signal path unchanged (see the resolved
isolator decision above). Level keeper
(`radio/levels.py`): AF config-pinned each heartbeat, SQL operator-owned with a deaf
clamp + bounded evidence-backed guard raises (never auto-lowered). Monitor-feedback
trap: mute the stream while keying.

- [ ] **2f-e — deferred polish:** Listen-live embed on `/radio` (WHEP = bare
      `fetch` + `RTCPeerConnection`, no heavy lib); Dahua camera `paths:` proxy
      entries so OBS pulls everything from the one hub.

## Phase 3 — Transmit console (operator-clicked TX) — DONE + DEPLOYED (RF bench passed 2026-07-21)

A `/radio` **Transmit** panel: a filesystem-driven **soundboard** (click a pre-staged
WAV) + **dual switchable TTS** (espeak-ng / piper) — every send is an attended button
press by KC3HEU (**R11**), no scheduler, manual ID baked into the audio. Built, deployed,
and field-tested; only optional polish (a piper voice model, a quantitative deviation
check) remains.

- [x] **PTT primitive + safety.** `Rigctld.set_ptt` + `radio/transmit.py`'s
      `keyed_tx` guard — `try/finally` release, independent-connection unkey retry,
      and a `MAX_TX_SECONDS` watchdog that force-unkeys over its own connection.
- [x] **TX audio render + play.** `render_tts` (espeak-ng/piper → ffmpeg loudnorm →
      48 kHz mono S16_LE), filesystem soundboard (`soundboard_dir`, normalize-on-play),
      `transmit_wav` (key → settle → `aplay` to the Digirig → unkey).
- [x] **Execution + logging.** `POST /api/radio/transmit` (clip|text) + `GET
      /api/radio/soundboard` (clips + available engines); an in-process lock 409s a
      concurrent send. `radio_transmissions.is_tx` marks TX rows; `archive_and_log`
      stores the clean source under `audio_dir()/tx/` with a derived waveform. A
      `.tx-active` sentinel (`transmit.tx_active`) makes the recorder drop its own
      loopback (`VoxGate.reset` + `Capture.discard`), so RX never double-logs.
- [x] **Frontend.** Transmit card on `/radio` (TTS box + soundboard grid + ON-AIR
      lock + manual-ID reminder; espeak/piper toggle shows only when piper is set) +
      `is_tx` badge in the log. Verified locally (render + reactive state, 0 console
      errors); rig-live test is item 5.
- [x] **RF de-risk (field) — PASSED 2026-07-21.** TX works well; **no Digirig USB
      crash even at HIGH power** — the 2a −71 was the unplug/replug, not transmitting
      (RF-ingress watch-item closed). Playback + capture coexist. Levels fine by ear;
      a *quantitative* deviation check (scope/meter) is the only remaining calibration,
      deferred as optional. Still recommended: set the rig's **TOT** as the hardware
      never-stuck-keyed backstop.
- [x] **Post-field tuning.** Key-up delay (default 250 ms) + TTS speech rate
      (**default 75 %**) are per-transmit UI sliders (`settle_ms`/`rate` in the POST,
      localStorage-persisted); rate maps to espeak wpm and piper length-scale.

**Deploy — DONE 2026-07-21** (kept as the rebuild record):

- **Ran the `is_tx` migration on the Pi *first*** — the transmission-log query
  selects `is_tx`, so pushing the code before the column exists would 500 the
  existing `/radio` log (same trap the `waveform` column had):
  `sqlite3 /mnt/nvme/data/gps_history.db "ALTER TABLE radio_transmissions ADD COLUMN is_tx INTEGER NOT NULL DEFAULT 0;"`
  (the `DEFAULT 0` backfills existing rows as RX — no script).
- **`apt install espeak-ng`** on the Pi (offline-cacheable, one-time online) — the
  default TTS engine. **piper** is optional: drop a voice model on NVMe and point
  `GPS_RADIO_PIPER_MODEL` at it (a manual install like the MediaMTX binary); until
  then the UI offers espeak only.
- **Create `/mnt/nvme/data/radio-tx/soundboard/`** and scp clips into it (any of
  `.wav/.mp3/.ogg/.m4a/.flac`; ffmpeg normalizes them).
- The deploy hook restarts `radio-recorder` (radio/ changed) + `gps-dashboard`
  automatically; no new service. `audio_dir()` must resolve identically in both the
  dashboard and recorder units (the sentinel rides on it) — already true via the
  shared `GPS_DB_PATH` default.
