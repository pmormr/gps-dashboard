# Radio Control Platform Plan (Icom ID-5100A)

> Living plan. Check items off as they land, record decisions inline. Markup
> welcome — leave comments against any row and we'll resolve them before writing
> code.
>
> **Iteration 1** (2026-06-24) — scoped the subsystem and **closed Phase 0
> feasibility on real hardware**. Locked the two-planes framing (R1), control-first
> ordering, **Hamlib rigctld as the CI-V layer** (R2), the udev-pinned enabled-gated
> daemon model (R3), and the recording data model sketch (R4). The OPC-478UC clone
> drives live CI-V over the radio's [SP2] jack — confirmed end-to-end. Open before
> Phase 1 coding: the control-UI scope and what the Hamlib ID-5100 backend actually
> exposes (enumerate via `dump_caps` first).

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
- **R5 — announcements are Part-97 gated (Phase 3).** Automated TX carries FCC
  obligations: station ID (§97.119), a control operator, automatic-control limits, no
  broadcasting/music. Designed in (call-sign ID injection + a sane trigger model), not
  bolted on. Recording your own RX is unencumbered.

## Phase 0 — Feasibility — **DONE (2026-06-24)**

- [x] Identify the OPC-478UC clone: **WCH CH343** (`1a86:55d3`, "USB Single Serial"),
      clean CDC-ACM enumeration (`/dev/ttyACM0` on the Pi), not a counterfeit PL2303.
- [x] Loopback-probe the wiring: **TX/RX bridged = single-wire CI-V topology.**
- [x] Confirm the jack from the manual: CI-V is on the **[SP2]** jack (3.5 mm); the
      2.5 mm **[DATA]** jack is GPS-NMEA/DV-data only. Manual vendored in `reference/`.
- [x] Decisive on-radio test: `tools/civ_probe.py` (committed) read the live VFO —
      **19200 baud, address 0x8C, 146.520 MHz.** Control plane proven.

## Phase 1 — Control (CI-V)

The buildable phase. Order roughly top-to-bottom.

- [x] **1a — Pi one-time — DONE (2026-06-24).** `apt install libhamlib-utils` →
      **Hamlib 4.5.4**. `rigctl -m 3071 dump_caps` enumerated the backend (cable not on
      the Pi yet; dumped via a pty so `rig_open` succeeds without the rig). Results below
      resolve **Q2** and reshape Q1.

  **ID-5100 backend (model 3071) capabilities — what the UI can use:**
  - **Frequency** get/set ✅ · **Mode** get/set ✅ (`AM FM FMN D-STAR AMN`).
  - **S-meter** = `RAWSTR` (0..255), get-only ✅. (No calibrated `STRENGTH` in the
    get-level set — read RAWSTR and render a relative bar.) Other levels: AF, SQL,
    RFPOWER, MICGAIN, VOXGAIN (get/set).
  - **PTT** get/set ✅ — **CI-V PTT keying works**, so Phase 3 TX needs no hardware PTT
    line. **DCD** (squelch open/closed) get ✅ — a clean **Phase 2 recording gate**.
  - **CTCSS/DCS** get/set ✅ · **Repeater duplex + offset** get/set ✅ · **VFO set** ✅
    (Main/Sub) · **Power on/off set** ✅ (get N).
  - **NOT supported:** memory channels (`set/get Mem`, `Bank`, `Channel`,
    `ctl Mem/VFO` all N; "Memories: None") — **memory recall is impossible via this
    backend.** `get VFO` N + `targetable VFO` N → no non-disruptive dual-band readout
    (reading the sub band means flipping the radio's active VFO each poll). Also N:
    Get Info, Scan, Split, Tuning-step get/set.
  - Serial: `4800..19200` 8N1 (matches the confirmed 19200/0x8C). Confirm the civaddr
    flag against the real rig in 1d (Hamlib defaults the ID-5100 civaddr; else
    `--set-conf=civaddr=0x8C`).
- [x] **1b — udev rule** `deploy/99-icom-civ.rules` (`1a86:55d3 → /dev/icom-civ`,
      `GROUP="dialout"`). Manual install (the hook copies only `*.service`/`*.timer`).
- [x] **1c — service** `deploy/radio-control.service`: `rigctld -m 3071 -r /dev/icom-civ
      -s 19200 -T 127.0.0.1 -t 4532 -C civaddr=0x8C`, enabled-gated (disabled by default).
      **Correction to the old note:** the hook's unit-cp is now a `deploy/*.service` glob,
      so the unit installs automatically — no manual cp needed. The enabled-gated restart
      stanza for the Pi hook is **still pending** (see 1h); until then a `radio-control`
      change won't auto-restart, which is fine while the service is disabled.
- [x] **1d — routes** `api/routes/radio.py` + the client `api/rigctld.py` (split out of
      the route for unit-testing): `/api/radio/status` + `POST /api/radio/{freq,mode,tone,
      repeater}`, `/radio` page. 502 on rig refusal, 503 when rigctld unreachable.
- [x] **1e — frontend** (built pre-SPA; the surface is now `web/src/views/Radio.svelte`
      + `web/src/lib/radio.ts`): main-band
      freq/mode/S-meter readout + set, CTCSS tone (off/tone/tsql + 50-tone dropdown),
      repeater shift/offset. Mobile-first; Radio added to every standalone-page nav.
- [x] **1f — tests:** `tests/test_rigctld.py` (parser + getters/setters via a fake socket,
      replies = real captured wire format) + `tests/test_radio_routes.py` (route JSON,
      offline path, 400/502/503 mapping). 28 tests; full suite 372 green, ruff+mypy clean.
- [x] **1g — docs:** CLAUDE.md — Radio Control section, `/api/radio/*` + `/radio` entries,
      structure-tree additions, Offline-Constraint note (Hamlib + udev rules are manual
      online installs the deploy hook skips).

- [x] **1h — live on-Pi validation — DONE (2026-07-02).** Cable on the Pi's USB + the
      radio's [SP2] jack; udev rule installed (`/dev/icom-civ → ttyACM0`); service enabled.
      `/api/radio/status` reads live freq/mode/RAWSTR and a freq set round-trips via the
      API. Findings: **`-C civaddr=0x8C` was redundant** (the model-3071 backend defaults
      to 0x8C — verified by reading the rig without the flag; dropped from the unit), and
      **rigctld defers the serial open until a client connects**, so the enabled unit runs
      cleanly even before the cable is plugged in (the API degrades to `online:false`).
      Testing note: with no get-VFO, the API reads/sets whichever band is *active* on the
      touchscreen — tapping the other band changes what the API sees.
- [x] **1i — deploy-hook stanza — DONE (2026-07-02).** Enabled-gated restart block added
      to the Pi's `post-receive` hook (before the drone-sync block), same gate pattern as
      OBD/Victron; validated end-to-end by pushing the 1h unit change.

**Phase 1 is fully closed** — control plane deployed, enabled, and live-validated.

## Phase 1.5 — Control-plane enrichment (CI-V only, no new hardware)

Approved 2026-07-02 after the Phase 1 close-out. Everything here rides the existing
rigctld daemon — Hamlib levels the API doesn't expose yet, plus raw CI-V frames via
rigctld's `send_cmd` passthrough for what the backend lacks (**R7**: raw CI-V goes
*through* rigctld, never a second serial client, preserving daemon-owns-the-port).
Source of truth for raw commands: the CI-V command table in the vendored manual
(`reference/ID-5100_ENG_CD_3.txt`, "Remote jack (CI-V) information", §13-17).

- [x] **1.5a — calibrated S-meter — DONE (2026-07-02).** RAWSTR `0000=S0, 0170=S9`
      per the manual (confirmed live — a strong signal read exactly 170). `/radio`
      renders real S-units (linear S0–S9, `S9+` above, S9 tick) via `web/src/lib/
      radio.ts`; `strength_db` dropped from `/api/radio/status` (Hamlib's generic
      Icom table, not ID-5100-calibrated; also one less CI-V transaction per poll).
- [x] **1.5b — volume + squelch + RF power — DONE (2026-07-02).** `AF`/`SQL`/`RFPOWER`
      in status (`levels`) + `POST /api/radio/level`; sliders + a Low/Mid/High TX-power
      segment on `/radio`. Live findings: **RFPOWER sets snap to 42/128/213 (÷255)** —
      the setting steps, distinct from the CI-V power-meter scale (26/77/255) — and
      **AF/SQL are per-band** (switching Main bands changes what the levels read; the
      2 s poll re-syncs the sliders, so no UI handling needed).
- [x] **1.5c — deterministic band select — DONE (2026-07-02).** `POST /api/radio/band
      {band: a|b}` sends raw CI-V `07 D0/D1` through `Rigctld.send_civ` (R7). Live
      wire-format capture: `send_cmd` replies put the `RPRT` terminator on the
      ``Reply:`` line (unlike every other command), so `send_civ` parses it directly
      — pinned in `tests/test_rigctld.py`. Verified live: pinning B/A flips what
      `get_freq` reads (and explained the Phase-1h "mystery freq change": the
      touchscreen had switched Main to the B band).
- [x] ~~**1.5d — D-STAR heard log (mini-phase).**~~ **DROPPED 2026-07-02** — Paul
      doesn't use D-STAR. (Was: poll cmd `20 02` → `radio_dstar_heard`, GPS-joined.)
      The CI-V DV-heard commands remain in the manual's table if this ever revives.
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

## Phase 2 — Transmission recording (Digirig ordered 2026-07-02)

- **Q4 RESOLVED (2026-07-02): Digirig Mobile + Icom RJ-45 cable kit** (kit lists the
  ID-5100 explicitly). One box covers Phase 2 RX + Phase 3 TX-audio/PTT at
  radio-appropriate levels. Wiring facts locked while choosing:
  - **RX tap = [SP1]** ([SP2] is the CI-V port now). SP1 carries the A+B mix and
    plugging it **disables the internal speaker** → Y-split SP1: one leg to a small
    cabin speaker, one leg (through a 10–20 dB pad) to the Digirig.
  - **TX audio = mic pin 6** (electret level, 8 V bias adjacent) + **PTT = pin 4**
    via the kit's RJ-45 leg — the manual has no line-in anywhere; the DATA jack is
    serial-only. CI-V PTT remains the primary keying path.
  - **Watch-item: ground-loop hum** (Pi + radio share van 12 V ground). Check the
    Digirig revision's isolation; escalation path = inline isolator on the speaker
    leg, then DRA-series/SignaLink.
  - Level stability synergy: the recorder can **pin AF via `POST /api/radio/level`**
    (1.5b) before capture for reproducible record levels.
- [x] Pick + order the USB audio interface (see Q4 above).
- [x] **2a — wire + smoke test — DONE (2026-07-15, direct SP1 leg, no Y-split yet).**
      The Digirig is a two-chip composite behind its own hub: C-Media codec
      `0d8c:0012` (mono S16_LE, 44.1/48 kHz) + CP2102N serial `10c4:ea60`. Clean
      capture end-to-end: AF pinned 0.15 via the API + capture gain 10/35 (≈0 dB)
      → open-squelch static at −16 dBFS peak / −28 RMS; closed-squelch floor
      −49 dBFS RMS. **C-Media power-on defaults are max gain (+23 dB) + AGC ON —
      the recorder must pin mixer state (AGC off, gain) at startup**, since a USB
      replug resets them.
  - **⚠ Dead-key incident (2026-07-15), the durable trap: the Digirig keys PTT
    whenever CP2102N RTS is asserted, and stock Linux asserts RTS on any port
    open.** On first plug-in BOTH ModemManager and gpsd's USB hotplug
    (`10c4:ea60` is a common GPS-dongle chip) grabbed the port and held
    RTS — the rig dead-keyed 146.520 at ~83 % power until the audio cable was
    pulled. The TX also crashed the Digirig's USB interface (error −71, needed a
    power-cycle) → **RF-ingress watch-item: first Phase 3 TX tests at low power.**
    Guard stack now live on the Pi: **ModemManager masked · `USBAUTO="false"` in
    `/etc/default/gpsd` (M9N is static `DEVICES`, unaffected) ·
    `deploy/99-digirig.rules`** (MM-ignore + `/dev/digirig` + ALSA card id
    `Digirig`; manual install like the other udev rules). Guards verified: replug
    with the mic leg connected no longer keys.
- [ ] **2b — boot-time RTS clearer.** Residual exposure: the CP2102N's virgin
      RTS state on a fresh boot/enumeration is untested (only ever observed
      post-clear). Tiny tool (open `/dev/digirig`, `TIOCMBIC` RTS+DTR, close) run
      from a udev-triggered oneshot so a reboot can never hold PTT. Build with 2c.
- [ ] **2c — `radio_transmissions` schema (R4) + a squelch/VOX-gated recorder
      process** (standalone, like the logger), writing audio files + GPS-snapped
      rows. Design walk-through first: gate choice (DCD poll reads the *main band
      only* while SP1 audio is the A+B mix — sub-band-only signals would go
      ungated; options DCD-only / audio-VOX / hybrid), pre-roll (continuous
      `arecord` pipe → ring buffer so the gate's poll latency doesn't clip
      openings), format/rate, NVMe file layout + retention. Recorder pins mixer
      state + AF level at startup for reproducible levels.
- [ ] **2d — map overlay (reuse 🚁 drone path) + a transmission log on `/radio`.**
- [ ] **Purchase:** 3.5 mm Y-splitter (restores the cabin speaker SP1 muted) +
      the 10–20 dB pad (decouples cabin listening volume from record level —
      without it, cranking AF for the speaker also cranks the Digirig leg).
- [ ] **Flagged (discuss before acting): CI-V-via-Digirig consolidation.** The
      Digirig serial port could replace the CH343 CI-V adapter (TX/RX bridged =
      same single-wire topology), freeing a USB port. Needs a separate CI-V
      cable for the Digirig serial jack, explicit `rts_state`/`dtr_state` OFF in
      rigctld (RTS = PTT on this port!), and live validation. Current CH343 path
      is deployed and proven — no urgency.

## Phase 3 — Announcements (needs TX audio + PTT)

- [ ] TX audio injection (mic jack) + PTT keying (CI-V PTT via rigctld, or hardware).
- [ ] Scheduler/trigger model + Part-97 station-ID injection (R5).

## Open questions (resolve in walk-through)

- **Q1 — Phase 1 UI scope. RESOLVED (R6).** Main band only; core + tone/repeater; no
  memory recall.
- **Q2 — Hamlib backend reality. RESOLVED (1a).** Freq/mode/S-meter(RAWSTR)/PTT/DCD/
  tones/duplex available; memory + get-VFO + scan + split + info unavailable. See 1a.
- **Q3 — service name. RESOLVED (R6).** `radio-control`.
- **Q4 — Phase 2 audio hardware. RESOLVED (2026-07-02).** Digirig Mobile + Icom RJ-45
  cable kit (ordered) — see Phase 2. **Q5 — Phase 3 PTT method:** CI-V PTT (proven in
  dump_caps) is primary; the Digirig's RJ-45 PTT line is the hardware fallback.
