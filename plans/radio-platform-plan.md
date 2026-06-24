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
  same). The `gps-radio` service stays **disabled** until the cable is wired, like
  `sensor-obd`/`sensor-victron`, so a host without the radio never crash-loops.
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

- [ ] **1a — Pi one-time:** `apt install libhamlib-utils` (while online; the deploy
      hook only `uv sync`s, so this is a documented manual step). Verify
      `rigctl --version` + `rigctl -m 3071 ... dump_caps` to **enumerate what the
      ID-5100 backend actually supports** (S-meter `l STRENGTH`, mode, memory recall,
      PTT) — this shapes 1d/1e scope. Confirm the CI-V-address flag (Hamlib likely
      defaults civaddr to 0x8C for this backend; else `--set-conf=civaddr=0x8C`).
- [ ] **1b — udev rule** `deploy/99-icom-civ.rules`: `1a86:55d3 → SYMLINK+="icom-civ"`,
      `GROUP="dialout"`. Mirrors `99-gps-dongle.rules`.
- [ ] **1c — service** `deploy/gps-radio.service`: `rigctld -m 3071 -r /dev/icom-civ
      -s 19200 -t 4532` bound to localhost, enabled-gated (disabled by default). Wire
      it into the post-receive hook's unit list — **the hook's unit-cp list is
      hardcoded, not a glob** (Victron lesson), so this needs a manual hook edit.
- [ ] **1d — routes** `api/routes/radio.py`: a small rigctld TCP client (stdlib
      socket) + `/api/radio/status` (freq/mode/S-meter), `POST /api/radio/freq`,
      `POST /api/radio/mode`, memory recall (scope per 1a). `/radio` page route.
- [ ] **1e — frontend** `templates/radio.html` + `static/js/radio.js`: current
      freq/mode/S-meter readout + set controls + memory list. Mobile-first, matches the
      `/gpsd` / `/sensors` standalone-page style.
- [ ] **1f — tests:** the rigctld response parsing is pure logic — unit-test it against
      canned protocol replies (mock socket), per the `tests/` "load-bearing pure logic"
      rule.
- [ ] **1g — docs:** CLAUDE.md — new Radio subsystem section, the `/api/radio/*` +
      `/radio` entries, structure-tree additions (`radio.py`, `radio.html`, `radio.js`,
      the two deploy units), and an **Offline Constraint** note that runtime apt deps
      (Hamlib) are installed during dev and aren't handled by the deploy hook.

## Phase 2 — Transmission recording (needs a USB audio dongle)

- [ ] Pick + wire a USB audio interface (RX audio off the speaker jack).
- [ ] `radio_transmissions` schema (R4) + a squelch/VOX-gated recorder process
      (standalone, like the logger), writing audio files + GPS-snapped rows.
- [ ] Map overlay (reuse 🚁 drone path) + a transmission log on `/radio`.

## Phase 3 — Announcements (needs TX audio + PTT)

- [ ] TX audio injection (mic jack) + PTT keying (CI-V PTT via rigctld, or hardware).
- [ ] Scheduler/trigger model + Part-97 station-ID injection (R5).

## Open questions (resolve in walk-through)

- **Q1 — Phase 1 UI scope.** Minimum useful: freq/mode/S-meter + memory recall? The
  ID-5100 is dual-band (A/B sub-bands) — control both, or just the main band to start?
- **Q2 — Hamlib backend reality.** Pending 1a `dump_caps`: does the ID-5100 backend
  expose S-meter and memory-channel recall over CI-V? Scope 1d/1e to what's real.
- **Q3 — service name.** `gps-radio` (gps- family) vs `radio-control`. Minor.
- **Q4 — Phase 2 audio hardware** (cheap CM108 dongle vs a proper interface) and **Q5 —
  Phase 3 PTT method** — defer to those phases.
