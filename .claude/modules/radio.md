# Radio — Icom ID-5100A control, recording, streaming, transmit

Control the van's **Icom ID-5100A** dual-band (2 m/70 cm, FM + D-STAR) transceiver
from the Pi, log received transmissions (GPS-joined), listen live over the network, and
transmit operator-clicked audio. The radio is a peer to the other telemetry streams and
reuses the project's shape: standalone Pi-side services + Flask routes + a `/radio` page,
GPS-joined like OBD/Victron.

The system splits into **four planes** across two physical interfaces:

| Plane | Interface | Service | Code |
|---|---|---|---|
| **Control** (freq/mode/tone/power/PTT) | CI-V serial | `radio-control` (rigctld) | `api/rigctld.py`, `api/routes/radio.py` |
| **Record** (RX capture → log) | Digirig USB audio (SP1) | `radio-recorder` | `radio/recorder.py`, `radio/vox.py`, `radio/levels.py`, `radio/waveform.py`, `radio/freqstate.py` |
| **Stream** (live listen) | Digirig USB audio (shared) | `radio-stream` → `mediamtx` | `deploy/mediamtx.yml`, `deploy/asound.conf`, `web/src/lib/radioListen.ts` |
| **Transmit** (operator console) | Digirig USB audio (mic) + PTT | *(in `gps-dashboard`)* | `radio/transmit.py`, `radio/ptt.py` |

Frontend is `web/src/views/Radio.svelte` + `web/src/lib/radio.ts` (+ `radioListen.ts`).
All three radio services are **enabled-gated** (dormant until the hardware is wired, like
`sensor-obd`/`sensor-victron`), so a host without the radio never crash-loops.

## Hardware & wiring

- **CI-V cable** = OPC-478UC *clone* = **WCH CH343** (`1a86:55d3`, clean CDC-ACM, not a
  counterfeit PL2303). Single-wire half-duplex on the **[SP2]** external-speaker jack (the
  manual's designated cloning port — **not** the 2.5 mm [DATA] jack, which is GPS-NMEA/DV
  only). **19200 baud, CI-V address 0x8C.** udev-pinned to `/dev/icom-civ`
  (`deploy/99-icom-civ.rules`).
- **Audio** = Digirig Mobile + Icom RJ-45 kit. RX tap = **[SP1]** (plugging it mutes the
  internal speaker → a Y-split is planned: one leg to a cabin speaker, one through a
  10–20 dB pad to the Digirig). TX audio = **mic pin 6**, hardware PTT = **pin 4**. The
  Digirig's serial side (CP2102N, `/dev/digirig`) is **PTT** (see the RTS trap below).
- **USB ground isolator** is inline on the Digirig (adopted 2026-07-21): the ~−50 dBFS
  input-referred floor was USB-domain digital contamination (a 1 kHz USB-SOF family +
  180 Hz–1 kHz hump *in the voice band*), not mains hum — the isolator cuts it ~13 dB to
  ~−64 with the signal path electrically unchanged (real SNR, no transformer in the audio
  path). An *audio*-domain isolator was shelved (only ~1 dB atop the USB one, and it puts
  a transformer bass-rolloff in the voice band). Both plateau ~−64/−68 ≈ the CM108 ADC
  floor. Probe: `tools/radio_floor.py` (`--open-squelch` grabs the receiver-static signal
  reference to separate real noise-removal from insertion loss).
- Manual: `reference/ID-5100_ENG_CD_3.pdf` (+ `.txt` extraction). Cable schematic:
  `reference/cable-RJ45.png`.

## Control plane — CI-V via rigctld

A long-lived **`rigctld`** (Hamlib model **3071**) owns the serial port and exposes
Hamlib's TCP text protocol on `127.0.0.1:4532`. The Flask routes speak that protocol
through a stdlib-socket client (`api/rigctld.py`) — **no** Python Hamlib binding; the
daemon-owns-the-port model solves serial contention (**R2**). `libhamlib-utils` is an apt
system dep, installed on the Pi while online (offline constraint governs *runtime*, not
cacheable dev-time deps).

**Capability map (`dump_caps`, model 3071):** freq/mode get+set, S-meter = `RAWSTR`
0..255 get-only (calibrated S0=`0000`/S9=`0170` per manual; no calibrated STRENGTH),
PTT get+set, DCD get, CTCSS/DCS + repeater duplex/offset get+set, AF/SQL/RFPOWER set.
**No memory channels, no get-VFO, no scan/split.** Modes: `AM FM FMN D-STAR AMN`.

**Main band only (R6).** get-VFO is unsupported, so the backend can't read which VFO is
active — a live dual-band readout would flip the active VFO each poll. The `/radio` page
reads/sets whichever band is Main on the touchscreen; tapping the other band changes what
the API sees. Endpoints: `GET /api/radio/status` + `POST /api/radio/{freq,mode,tone,`
`repeater,level,band,dualwatch,stage_crossband,power}`.

**Raw CI-V goes *through* rigctld (R7)** via `send_cmd`, never a second serial client
(that would fight for the port). Wire quirks pinned in tests: `send_cmd` replies put
`RPRT` on the `Reply:` line; **reads of raw commands must use rigctld's *non-extended*
`send_cmd_rx`** — the extended `+\` form returns no reply bytes. Raw commands in use:
band-select `07 D0`/`D1`, VFO-mode-select bare `07`, dualwatch `16 59`, power `18`.

**VFO-select-before-every-tune.** Bare `07` = absolute "Select VFO mode" (idempotent when
already in VFO, **not** a toggle). There is **no** CI-V command to select Memory mode
(`08` is absent from the ID-5100 table), and `07 D0`/`D1` preserve the band's Memory/Call
state — so a `set_freq` on a Call channel just overrides the displayed freq under the
channel label (the "2m call" cosmetic bug). Fix baked in: **every freq set sends bare `07`
first** (`_tune` in `api/routes/radio.py`). App-side presets always stage into VFO; the
rig's own memories are never involved.

**CI-V power off/on (R6/1.5e).** `POST /api/radio/power {on}` → raw CI-V `18`:
off = `FE FE 8C E0 18 00 FD`; on = a **25× `0xFE` wakeup preamble** (19200 baud → 25 per
manual §13-17) + `18 01`. Both directions validated on the rig. **Hamlib's own
`set_powerstat` is an unreliable no-op on model 3071** — the raw path is what's used. The
rig exposes **no readable power state** (both `get_powerstat` and `get_freq` return cached
values), so it's **fire-and-forget**: the `/radio` Rig-power card is two action buttons
(power-off two-tap-confirmed), not a toggle. Power-cycling reverts SQL to the marginal
~0.165 and resets TX power (manual note *3); the recorder's LevelKeeper re-asserts SQL
within ~60 s.

## Record plane — VOX-gated recorder

`radio/recorder.py` (`radio-recorder` service) VOX-gates the Digirig's SP1 capture (the
A+B mix) into pre-rolled WAVs + GPS-snapped `radio_transmissions` rows.

**Audio-VOX is the gate (R8).** RMS on the SP1 stream itself, so the trigger hears exactly
what gets recorded (including sub-band traffic in the A+B mix). DCD + freq/mode are polled
once at gate-open as *metadata only* — the suspect pegged-RAWSTR/DCD-false anomaly can
never drop a recording. Continuous `arecord` (native mono S16_LE 48 kHz) → ~3 s ring-buffer
pre-roll → VOX on ~100 ms RMS blocks → stdlib-`wave` WAV under
`/mnt/nvme/data/radio-audio/YYYY-MM/` (dir derives beside the DB, env-overridable). A
retention pruner NULLs `audio_path` but keeps the row (the log outlives the audio),
deliberately outside the backup path like tiles. No band column: get-VFO is unsupported,
so which band is Main is unreadable — freq/mode is the active-main-band readout and
`dcd_main` marks the tag's confidence. GPS snap = latest raw fix, direct DB read, NULL when
stale (>5 min).

**Commit rule: activity separates, level cannot (R9).** Corpus analysis of the first live
day proved false triggers (squelch crackle, rig beeps) are single 100–500 ms transients at
*exactly voice level* (−30.3 vs −30.2 dBFS median loud block) — **no OPEN-threshold raise
can separate them.** What separates is total above-threshold activity per capture: blips
measured 1–5 loud blocks, voice 10–12. The gate commits a capture on close only when it
accumulated **≥ `GPS_RADIO_MIN_LOUD_BLOCKS`** (default 6 ≈ 600 ms) blocks at the open
threshold, else discards; pending captures buffer in **RAM**, so a discard never touches
disk or DB. DCD is polled ~1 Hz across active captures (any open reading → `dcd_main=1`),
fixing the gate-open-beats-the-squelch miss. Accepted trade: an isolated one-word
transmission (~3–4 blocks) discards too — `GPS_RADIO_MIN_LOUD_BLOCKS` is the knob.
(ZCR turned out a weak discriminator — the rig's speaker path band-limits even static;
noted so nobody re-derives it. FM-quieting detection stays a pocket v2.)
`tools/radio_vox_replay.py` rescores stored WAVs by the same rule (`--purge` deletes).

**VOX thresholds** default −52 (open) / −56 (close) dBFS after the USB-isolator re-tune
(measured floor-block ceiling −58.1: OPEN clears it ~6 dB; CLOSE stays *above* the floor so
floor blocks can't hold the gate open — a naive "same ratio" CLOSE below −58 would break).
~2 s hang, hard per-file cap so a stuck carrier can't grow an unbounded file. The recorder
re-pins the C-Media mixer (AGC off, gain) every session start — a USB replug resets it to
max gain + AGC ON — so a replug re-pins by construction.

**Waveform** (`radio/waveform.py`): a record-time fixed-N 0..255 envelope (absolute dBFS
window) on the `waveform` column, derived from sub-block peaks — drawn as the log/player
strip and survives the audio prune. Tuned to `WAVEFORM_SUBBLOCKS=10` (~100/s) + 960
buckets; rendered as a single SVG `<path>`.

Log UI: `GET /api/radio/transmissions` (newest-first keyset paging `before_id`, `min_s`
blip filter, `has_audio`) + `GET .../transmissions/<id>/audio` (Range-capable playback;
pruned/missing → 404, traversal-guarded). Freq/mode tags render dimmed unless
`dcd_main=1`. **No map surface, by choice** — rows keep lat/lon if it ever revives via the
drone-overlay path.

## Stream plane — live listen (R10)

**MediaMTX is the media hub** (`mediamtx` service; single static arm64 binary at
`/mnt/nvme/mediamtx/`, config `deploy/mediamtx.yml` read from the deploy checkout so edits
deploy on push). An enabled-gated ffmpeg publisher (`radio-stream`) feeds it: shared
Digirig capture → Opus 48 kHz mono ~48 kbps → RTSP publish on localhost. Listeners attach
to the **hub**, never the publisher:

- VLC/OBS `rtsp://<pi>:8554/radio` (~0.5–1 s)
- any browser `http://<pi>:8889/radio` (WebRTC, sub-second, no STUN on the LAN)
- the `/radio` **Listen-live card** — a bare `fetch` + `RTCPeerConnection` WHEP client
  (`web/src/lib/radioListen.ts`, no signaling lib): non-trickle (host candidates only),
  POST the SDP offer to `.../radio/whep`. Cross-origin works (MediaMTX v1.19.2 sends
  `Access-Control-Allow-Origin: *` on WHEP); teardown is client-side `pc.close()`, never
  WHEP DELETE (buggy in this version). Independent of the CI-V readout — the stream plane
  stands alone.

**Capture sharing = ALSA dsnoop** (`deploy/asound.conf` → `/etc/asound.conf`, manual
install like the udev rules): the hw codec is single-open; dsnoop lets recorder + publisher
capture independently, so listening works while `radio-recorder` is stopped or being
reconfigured (the tuning scenario). Anything else that captures the Digirig **must** go
through `digirig_shared` too. The hub is also the intended aggregation point for Dahua
camera proxying later (see `plans/cameras-plan.md`, `plans/streaming-platform-plan.md`).

## Transmit plane — operator-clicked console (R11)

A `/radio` **Transmit** panel: a filesystem soundboard
(`/mnt/nvme/data/radio-tx/soundboard/`, any of `.wav/.mp3/.ogg/.m4a/.flac`, ffmpeg-
normalized) + **dual switchable TTS** — espeak-ng default (the offline formant synth), piper
the natural-voice alternate behind `GPS_RADIO_PIPER_MODEL`. `POST /api/radio/transmit`
(`clip` | `text`+`engine`+`rate`, `settle_ms`, `keyer`) + `GET /api/radio/soundboard`.

**Attended-only, no scheduler (R5/R11).** Every send is a button press by the licensed
control op (**KC3HEU**) — no beacon/scheduler, which sidesteps §97.109 automatic-control
and §97.113(b) broadcasting entirely. **Callsign ID is operator-manual** — baked into the
text/clips, never auto-injected.

**Never-stuck-keyed invariant.** A wedged transmitter is the worst failure mode (illegal,
jams the freq, cooks the finals). `radio/transmit.py` `keyed_tx` asserts PTT *before* its
`try/finally` (a keying failure leaves nothing keyed), releases in `finally`, retries the
unkey over an independent connection, and a `MAX_TX_SECONDS`=120 watchdog force-unkeys over
its **own** rigctld connection. Set the rig's hardware **TOT** as the backstop. TX audio:
`render_tts` (→ ffmpeg `loudnorm=I=-16:TP=-1.5` → 48 kHz mono s16le; consistent loudness =
consistent deviation), key → settle (default 250 ms) → `aplay` to the Digirig → unkey.

**Self-TX is logged from the clean source** (the exact rendered bytes, not the SP1
loopback): one `radio_transmissions` row with `is_tx=1`, clean WAV under `audio_dir()/tx/`.
A **`.tx-active` sentinel** makes the recorder drop its own loopback (`VoxGate.reset` +
`Capture.discard`) so RX never double-logs. An in-process `threading.Lock` 409s a
concurrent send (the app is single-process). Post-field tuning: key-up delay + TTS speech
rate (default 75 %) are per-transmit UI sliders (`settle_ms`/`rate`, localStorage-persisted;
`rate` → espeak wpm / piper length-scale).

**Two keyers.** CI-V PTT (`set_ptt`) is the default — chosen over the Digirig RTS line so
the RTS-guard stack stays untouched. But CI-V is NAK'd in Repeater Mode, so an explicit
**RTS keying mode** (`radio/ptt.py` `keyed_tx_rts`, `keyer:"rts"` per-transmit) keys the
Digirig's RTS line directly — the *only* path that transmits while cross-band repeating.
Same never-stuck invariant on the RTS line (finally-deassert + independent watchdog +
fd-close hangup; reuses the `TIOCMBIS`/`TIOCMBIC` mechanism from
`tools/digirig_clear_rts.py`). This is a **deliberate** use of the RTS=PTT trap, not a
casual open. Bench tool: `tools/radio_rts_ptt.py`. An unkeyable RTS device → 503.

## Cross-band Repeater Mode

**Repeater Mode is touchscreen-only in *every* direction** — CI-V can't enter, exit, or
power-cycle out of it (validated on the rig). In `[MONI]`: `set_freq` → `RPRT -9`
(rig-rejected); `07` and `16 59 00` → `RPRT 0` but **ignored** (no display change);
`18 00` (power-off) → transmitted OK but the rig stayed on. The physical power button is
the only remote-less exit. So the app **stages before the operator engages**, never drives
the mode itself.

- **One-tap staging.** `POST /api/radio/stage_crossband` applies, in one rigctld
  connection: for each band → pin as Main (`07 D0`/`D1`) → select VFO (`07`) → set
  freq/mode/tone; then TX power → dualwatch ON (`16 59 01`) → restore the chosen Main. Any
  refusal aborts (502). The `/radio` "Cross-band repeater" card has a "Stage race net"
  preset (147.555 2m ↔ 446.175 70cm TSQL 203.5) plus the manual A/B form.
- **Transmit in Repeater Mode** = the RTS keyer (above); bench-verified to transmit
  injected mic audio while cross-band repeating (confirmed on a second radio).
- **Freq inference** (`radio/freqstate.py`). CI-V is NAK'd, so a capture/TX in Repeater
  Mode would log a blank freq — but you can't retune without exiting (which restores CI-V
  and refreshes the read), so the **frozen last-online freq *is* the true repeater freq**,
  and a repeater signal is on **both** bands at once. A shared DB row **`radio_freq_state`**
  holds `remember_main` (frozen from the last successful read, self-corrects when control
  returns) + `remember_staged` (the A/B pair from the last stage); `infer_freq` returns the
  frozen main + the staged other band *only* when the pair's main still matches the frozen
  freq (stale-after-retune → main-band only). Shared because the recorder is a **separate
  process** from the web app: the web app writes it (status polls + stage), the recorder
  both writes (each live read) and reads (`open_capture` infers when `rig_snapshot` is
  empty → `radio_transmissions.freq_b_hz`). The `/radio` log renders `146.520 ↔ 445.000 ·
  repeater` (source-agnostic — TX and RX look identical). **Caveat:** a fresh deploy *while*
  in Repeater Mode leaves the store empty (no successful CI-V read to seed it); seeds on the
  next normal-mode read or an app-side stage.
- **Status honesty.** In Repeater Mode `get_freq` returns `RPRT -9`, which `status()` used
  to read as a dead cable. Now a non-None `RigctldError.rprt` means the daemon reached the
  rig and it answered (on the bus, refusing) → `reachable:true`+`rprt` in
  `/api/radio/status`, distinct from a transport outage (rprt None: daemon down / cable
  dead / timeout). The `/radio` banner splits three ways: amber "in Repeater Mode — exit on
  the touchscreen" (`reachable && !online`) / red "rigctld up but rig silent" / red "service
  disabled". The same `inRepeater` derived drives the Transmit-card RTS nudge and the
  Rig-power-card honesty note.

## Levels & calibration

- **AF = record calibration, config-owned;** SQL = operator-owned. The rig forgets
  CI-V-set levels across a power cycle, so `radio/levels.py` `LevelKeeper` (pure/clockless —
  heartbeats + blocks are the clock) re-asserts **AF** every heartbeat (a bumped volume knob
  reverts in ≤60 s) and pins **AF=0.25** (`GPS_RADIO_PIN_AF`) — the capture-chain floor is
  AF-invariant, so AF is free SNR until static clips (static peak −4.6 @ 0.25 is the
  ceiling; 0.35 clips).
- **SQL keeper** never fights the operator: memory (adopt each reading; restore the
  operator's own value after an offline gap), deaf clamp (>`GPS_RADIO_SQL_SANE_MAX` 0.5
  never adopted — silence has no evidence, the value is the tell), and bounded evidence-
  backed **guard raises** (≤`GPS_RADIO_GUARD_MAX_SQL` 0.35, **never auto-lowered**): flap
  storm ≥15 discards/10 min → +0.03/10 min; stuck-open static = unbroken ≥3 min run with
  block-rms stddev < 2.5 dB (voice breathes, static doesn't) → +0.03/2 min until the gate
  closes. **Marginal squelch (0.165) is the first knob to check if chatter returns** — the
  2e "noise storm" was a marginal main-band squelch, killed dead by SQL→0.25.
- **Monitor-feedback trap:** mute the live stream while keying — an open-air monitor rides
  a tone onto the TX audio. The Listen-live card auto-mutes while `sending`.

## Traps

- **RTS = PTT (non-negotiable).** Asserting RTS on the Digirig serial port (`/dev/digirig`)
  hardware-keys TX. The 2a dead-key incident: on first plug-in both ModemManager *and*
  gpsd's USB hotplug (10c4:ea60 looks like a GPS dongle) grabbed the port and held RTS → the
  rig dead-keyed 146.520 at ~83 % power and crashed the Digirig USB (−71). **Nothing may
  open `/dev/digirig` casually.** The guard stack is load-bearing: ModemManager masked,
  gpsd `USBAUTO=false`, `deploy/99-digirig.rules` (MM-ignore + `/dev/digirig` symlink + ALSA
  card id), and the `digirig-rts-clear` udev oneshot (`tools/digirig_clear_rts.py`, fires
  ~1 s after enumeration → RTS/DTR clear). A bare enumeration open blips RTS ~ms — kernel
  behavior, accepted; the reboot test confirmed it doesn't meaningfully key TX. But an
  open that *stays* open keys for its whole duration: the 2026-08-08 kerchunk incident was
  `sensor-obd`, pinned to a bare `/dev/ttyUSB0` that a hub re-enumeration handed to the
  Digirig, running python-OBD's multi-second ELM327 baud negotiation against it every ~10 s
  — an unattended transmitter for a day. Fixes: `deploy/99-obdlink.rules` pins the adapter
  to `/dev/obdlink`, and `sensors/obd_reader.py` refuses by realpath any port that resolves
  to `/dev/digirig` (and refuses python-OBD auto-scan, which opens every `/dev/ttyUSB*`).
  **No non-radio service may name a bare `/dev/ttyUSB*`** — pin every serial consumer to a
  udev symlink. The only intentional RTS assert is the `keyed_tx_rts` transmit path.
- **Baud/power cycle:** power-cycling the rig reverts SQL to ~0.165 and resets TX power; the
  CI-V power-on preamble is baud-specific (25× `FE` at 19200).
- **Deploy DDL-first:** the transmission-log query selects added columns, so a
  schema-adding push must run the `ALTER` on the Pi **first** (the `is_tx`, `freq_b_hz`,
  `waveform` columns each hit this — pushing the code before the column exists 500s the
  existing `/radio` log). `radio_freq_state` is auto-created by `init_db`.
- **`audio_dir()` must resolve identically in the dashboard *and* recorder units** (the
  `.tx-active` sentinel and the freq-state row ride on it) — true via the shared
  `GPS_DB_PATH` default; if `GPS_RADIO_AUDIO_DIR` is ever set, set it in **both**.
- **New service = new hook block.** The post-receive hook installs all `deploy/*.service`
  on a `deploy/` change (glob) but *restarts* per-unit — a brand-new radio service needs its
  restart block added to the hook on the Pi (`radio-control` restarts on its unit only,
  `radio-recorder` on `radio/` or its unit, `mediamtx` on its unit or `deploy/mediamtx.yml`,
  `radio-stream` on its unit).
- **System deps not carried by `uv sync`** (install online, one-time): `libhamlib-utils`
  (rigctld), `alsa-utils` (`arecord`/`amixer`/`aplay`), `ffmpeg` + the MediaMTX static
  binary, `espeak-ng`. Non-unit config the hook does *not* copy: the udev rules and
  `asound.conf` (→ `/etc/asound.conf`).

## Deferred / open

- **Y-splitter + 10–20 dB pad** (purchase) — restores the cabin speaker off SP1 and
  decouples cabin listening volume from record level; **re-run the AF calibration when it
  lands.**
- **Piper voice model** (optional) — until a model is dropped on NVMe + `GPS_RADIO_PIPER_MODEL`
  set, the UI offers espeak only.
- **Quantitative deviation check** (optional) — TX levels are fine by ear; a scope/deviation
  meter is the only way to verify absolute deviation. RF-ingress watch-item is closed (no
  Digirig USB crash even at HIGH power — the 2a −71 was the replug, not transmitting).
- **Dahua camera `paths:` proxy** — owned by `plans/cameras-plan.md` /
  `plans/streaming-platform-plan.md`; blocked on a secrets story (RTSP creds can't live in
  the committed `mediamtx.yml`; MediaMTX does no env-substitution → needs a runtime-
  templating step).
- **CI-V-via-Digirig-serial consolidation** (flagged, discuss first) — the Digirig serial
  port could replace the CH343 CI-V adapter (TX/RX bridged = same single-wire topology),
  freeing a USB port; needs a separate CI-V cable, explicit `rts_state`/`dtr_state` OFF in
  rigctld (RTS = PTT!), and live validation. The CH343 path is deployed and proven — no
  urgency.

## Design decisions (R-numbers)

Load-bearing: code comments across `radio/`, `api/`, and `deploy/` anchor to these numbers.
Distilled from the radio platform plan (now dropped; see git history for the play-by-play).

- **R1 — Two planes, control-first.** Build control (CI-V) first; it's de-risked and needs
  no new hardware. Recording and announcements are the audio plane, gated on a USB sound
  interface.
- **R2 — Hamlib `rigctld` is the CI-V layer.** Model **3071**. A long-lived daemon owns the
  serial port and exposes the TCP text protocol on `127.0.0.1:4532`; Flask speaks it with a
  stdlib socket — **no** Python Hamlib binding. Rationale: free, battle-tested command set,
  and daemon-owns-the-port cleanly solves serial contention. (Chosen over a pure-Python CI-V
  module built on `civ_probe.py`.)
- **R3 — udev-pinned, enabled-gated daemon.** A udev rule pins the cable (`1a86:55d3`) to
  `/dev/icom-civ`. `radio-control` stays disabled until the cable is wired, so a host
  without the radio never crash-loops.
- **R4 — recording data model (superseded by R8's schema).** The original
  `radio_transmissions` sketch; lat/lon snapped from the live GPS stream, audio on NVMe, row
  is metadata. R8 dropped the sketched band column.
- **R5 — announcements are Part-97 gated.** Automated TX carries FCC obligations (station ID
  §97.119, control operator, automatic-control limits, no broadcasting/music). Designed in,
  not bolted on. Recording your own RX is unencumbered. (Superseded in practice by R11's
  attended-only model, which sidesteps the automatic-control hazards entirely.)
- **R6 — Phase 1 scope.** **Main band only** (get-VFO unsupported → a dual-band readout
  would flip the active VFO each poll). Core + tone/repeater: freq/mode/S-meter readout +
  set, CTCSS/DCS tone, repeater duplex/offset. **No** memory recall (backend exposes none).
  Service **`radio-control`** (not the gps- family).
- **R7 — raw CI-V goes through rigctld.** Raw commands are sent via `send_cmd`, never a
  second serial client (that would fight for the port). Reads use the *non-extended*
  `send_cmd_rx`.
- **R8 — recorder design: audio-VOX primary, DCD-as-metadata.** RMS on the SP1 stream is the
  gate, so the trigger hears exactly what's recorded (incl. sub-band A+B traffic); DCD +
  freq/mode are polled once at gate-open as metadata only, so the pegged-RAWSTR/DCD-false
  anomaly can never drop a recording. Continuous `arecord`, ~3 s ring pre-roll, VOX on
  ~100 ms RMS blocks, native-format WAV + age/size pruner that NULLs `audio_path` keeps rows.
  No band column: which band is Main is unreadable, `dcd_main` records tag confidence. GPS
  snap = latest raw fix, NULL when stale.
- **R9 — commit rule: activity separates, level cannot.** False triggers (crackle, beeps)
  are single 100–500 ms transients at *exactly* voice level, so no OPEN-threshold raise
  separates them; total above-threshold activity does (blips 1–5 loud blocks, voice 10–12).
  Gate commits only at ≥ `GPS_RADIO_MIN_LOUD_BLOCKS` (default 6 ≈ 600 ms); pending captures
  RAM-buffer so a discard never touches disk/DB. DCD polled ~1 Hz across active captures.
  (ZCR = weak discriminator, the speaker path band-limits even static; FM-quieting stays a
  pocket v2.)
- **R10 — live listen / streaming: MediaMTX is the media hub.** A single static arm64 binary
  on NVMe, fed by an enabled-gated ffmpeg publisher (`radio-stream`, Opus 48 k mono → RTSP
  localhost); listeners attach to the hub (VLC/OBS RTSP, browser WebRTC, the `/radio` WHEP
  embed), never the publisher. Multicast rejected (WiFi base-rate/no-retransmit, dies at the
  HaLow bridge; the camera plan it would "mirror" is really RTSP unicast → mirror the
  *hub*). Capture sharing = ALSA dsnoop (the hw device is single-open), so listening works
  while the recorder is stopped. (Chosen over an in-app chunked-Ogg Flask route and over
  Icecast.)
- **R11 — transmit console: operator-clicked, not automated.** TX is attended remote control
  only — every transmission is a `/radio` button press by the control op (**KC3HEU**); no
  scheduler/beacon, which sidesteps §97.109 automatic-control and §97.113(b) broadcasting.
  Inputs: a filesystem soundboard + dual switchable TTS (espeak-ng / piper). PTT = CI-V by
  default (keeps the RTS-guard stack untouched), with an explicit RTS keying mode for
  Repeater Mode; `try/finally` release + max-duration watchdog enforce the never-stuck-keyed
  invariant. Self-TX logged from the clean source; a TX-active sentinel suppresses the
  recorder so RX never double-logs. Callsign ID operator-manual.
