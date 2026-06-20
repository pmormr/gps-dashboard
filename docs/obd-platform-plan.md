# OBD-II Vehicle Telemetry Plan

> Living plan. Check items off as they land, record decisions inline. Markup
> welcome — leave comments against any row and we'll resolve them before writing
> code.
>
> **Iteration 1** — initial cut. Locked the swappable-reader framing (O1), the
> Pi-side driver model (O2), MQTT-bus reuse (O3), the registry + wide
> `obd_readings` data model (O4), engine-state-gated polling as the drain fix
> (O5), `python-OBD` as the (pending-approval) library (O6), the live-gauge
> fast-poll path (O7), and the MPG = OBD-fuel × GPS-distance synergy (O8). Phase 0
> (de-risk with the BAFX, no purchase) is the immediate actionable; everything
> downstream hinges on what that session reveals.

## Context

The app logs GPS (`gps_points`) and environmental sensors (BME680 → `bme680_readings`)
into one SQLite DB, GPS-correlatable on a shared millisecond timestamp grid. This
plan adds a **third stream: the van itself**, read over OBD-II — engine RPM, vehicle
speed, coolant/intake temps, manifold pressure, load, throttle, fuel level, fuel
rate, and chassis-battery voltage, plus diagnostic trouble codes (DTCs).

The payoff is the same correlation thesis the project already runs on: vehicle
telemetry **plotted along the GPS route and over time** — engine load vs. terrain
grade, OBD speed vs. GPS speed, and **real per-trip fuel economy** (OBD supplies the
fuel leg, the GPS track supplies the distance leg — O8). Plus live driving gauges and
battery/health watch (voltage, coolant, check-engine).

Crucially, OBD is **not a new platform** — it slots into the existing sensor
platform (`mqttbus/` ingest + the `sensors` registry + the `/sensors` charts). The
new work is a Pi-side reader, one wide table, and a display surface. This doc treats
OBD as "the van as a sensor" — exactly the framing that motivated it.

A later, cleaner sibling stream — the **Victron GX** house-power system (native
MQTT) — drops into the same pattern once a network cable reaches it; it's noted as a
future phase, and is *why* the data model is built generically now.

---

## Hardware

### The vehicle — 2021 Ram ProMaster 2500

- **Engine:** 3.6 L Pentastar V6, **gasoline** (the US ProMaster dropped the EcoDiesel
  after 2018). FCA/Stellantis, Fiat Ducato platform.
- **OBD protocol:** standard **CAN ISO 15765-4** @ 500 kbaud — the ELM327 auto-detects
  it; no protocol fiddling.
- **Speed-density, no MAF.** The Pentastar runs a **MAP sensor + IAT**, *not* a
  mass-airflow sensor — so PID `10` (MAF) is expected to be **unsupported**, which
  shapes the fuel-rate approach (see O8 / open decision B). Authoritative answer comes
  from the Phase 0 supported-PID query, not from this assumption.
- **FCA Security Gateway (SGW).** 2018+ Stellantis vehicles add a Secure Gateway
  Module that blocks *write* access (clearing codes, actuator tests) through the OBD
  port. Read-only live-data polling — exactly this use case — **generally** passes,
  but with enough model-year variance that **Phase 0 must confirm reads work on this
  van** before any hardware spend. (If reads are blocked, a 12+8 SGW-bypass harness
  exists, but it's invasive and almost certainly unnecessary for passive logging.)

### The reader(s) — a swappable component (O1)

Every option speaks the same interface: **ELM327 AT-commands over a serial stream**.
The reader is therefore swappable behind one driver; the code does not change per
dongle. Validate with what's on hand, upgrade as an isolated swap.

| Reader | Conn | Chip | Role | Notes |
|--------|------|------|------|-------|
| **BAFX Bluetooth** (owned) | BT-Classic SPP | ELM327 clone | **Phase 0 validation** | Free; settles SGW-reads + PID support today. Weaknesses (slow, no true sleep → parasitic draw, flaky rfcomm reconnect) are why we don't keep it as the permanent reader. |
| **OBDLink EX** (incoming) | **USB** | STN2120 | Permanent install | Deterministic `/dev/ttyUSB0`, udev-pinnable like the GPS; no radio, no pairing. Wired is the right call for a fixed van install. |

WiFi dongles (make their own AP, fight the van LAN) and BLE dongles (custom GATT,
more Linux integration work) are both rejected for this install.

### Parasitic drain (O5)

The Pi runs **24/7 off the house battery**, but the dongle hangs off the OBD port's
**always-on pin (16)**, so it drains the **starter/chassis** battery independently —
and software "stop polling when parked" won't kill a cheap clone's ~20–45 mA
quiescent draw. Two clean fixes, not mutually exclusive:

1. **Engine-state-gated polling** (O5, software): when the engine is off (no
   response / RPM 0), the reader stops querying and **closes the connection** so the
   bus can sleep. Also bounds data volume — we only log while the van runs (~5% of
   the time).
2. **Ignition-switched feed to the dongle** (hardware, optional but robust): interrupt
   pin 16 through a relay driven by an engine-running signal (possibly off the
   DC-DC charger / GX). Dongle fully dead when parked → **zero** drain, and it makes
   the **free BAFX viable long-term**. The OBDLink EX's STN low-power sleep is the
   buy-it-away alternative if we skip the relay.

---

## Confirmed decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| O1 | Reader coupling | **Swappable component behind one ELM327/serial driver** | Validate with the owned BAFX; upgrade to the OBDLink EX (USB) as an isolated swap. No code change per dongle. |
| O2 | Where the driver runs | **Pi-side reader** (not an ESP32 node) | The ELM327 is a dumb serial bridge that needs a host; the Pi is in the van. Mirrors the legacy `sensors/bme680.py` Pi-attached publisher, *not* the remote ESPHome nodes. |
| O3 | Transport | **Reuse the MQTT bus** — publish `sensors/van/obd` → existing `mqttbus/ingest.py` → SQLite | The platform is built for exactly this; reuse gives free liveness (LWT), queuing across restarts, and a path to the live/alarm features. One extra hop vs. direct-write, accepted for consistency. |
| O4 | Data model | **Reuse the `sensors` registry + a wide `obd_readings` per-type table** on the canonical ms grid | Registers as `(node='van', type='obd')`, one row; the wide table joins cleanly to `gps_points`/`track_points` and slots into ingest's `READING_TABLES` dispatch and the `/sensors` uPlot charts — almost entirely free. |
| O5 | Parasitic drain | **Engine-state-gated polling** (close the connection when off), optionally **ignition-switched dongle feed** | Pi is 24/7 on house power, so an always-on-pin dongle drains the *starter* battery; gating + a relay (or the EX's sleep) solves it. Gating also bounds volume to drive-time. |
| O6 | Library | **`python-OBD`** *(pending dep approval — O-dep)* | De-facto standard: ELM327 handshake, protocol auto-detect, supported-PID discovery, async polling, unit conversion. Raw-pyserial AT-commands is the zero-dep fallback. |
| O7 | Live gauges | **`/api/obd/latest` fast-poll (~1 Hz)**, mirroring `/api/points/latest` | Sidesteps the still-open MQTT-over-WS broker blocker (sensor-platform Phase 3b / decision F); the GPS live-dot already proves the pattern. |
| O8 | Fuel economy | **OBD fuel leg × GPS distance leg** | OBD distance PIDs are coarse and reset on code-clear; the processed GPS track already yields high-quality distance. Fuel rate from PID `5E` if supported, else derived (see open decision B). |

---

## Constraints carried from the project

- **GPS logging is sacred.** The OBD reader is a new, independent publisher; it never
  touches gpsd, the logger, or `gps_points`. A wedged dongle or BT stack can't affect
  GPS capture.
- **Offline-first.** Pure local; no internet. `python-OBD` installs from `uv.lock` at
  deploy (Python deps need no vendoring — only frontend JS does). No new CDN calls.
- **Same DB.** `obd_readings` lives in `/mnt/nvme/data/gps_history.db` so OBD↔GPS and
  OBD↔sensor stay local joins.
- **Reuse what exists.** Timestamps go through `api.db.canonical_timestamp` (ms,
  fixed-width) so OBD rows join `gps_points` directly. The reader follows the logger
  ethos: auto-reconnect, periodic heartbeat with dropped-reading reason counters,
  graceful shutdown. `tools/` scripts handle `KeyboardInterrupt` → exit 130.
- **Deploy model.** A new `deploy/sensor-obd.service` (or `obd-reader.service`) slots
  into the post-receive hook with **enabled-gated restart**, exactly like
  `sensor-bme680` / `mqtt-ingest` — a host without OBD never crash-loops, and a
  reader fault is non-fatal to the core GPS deploy.

---

## Architecture

```
                                       ┌─→ ingest subscriber ──→ SQLite (obd_readings)
  van OBD-II port                      │   (existing mqttbus/ingest.py; +READING_TABLES entry)
       │                               │
  [ELM327 dongle]                      │
   BAFX (BT/rfcomm) → /dev/rfcomm0     │
   OBDLink EX (USB) → /dev/ttyUSB0     │
       │                               │
  [Pi: sensors/obd.py] ──publish──→ mosquitto ──sensors/# ──┤
   python-OBD, engine-gated poll      :1883                  │
   sensors/van/obd  + .../status LWT                         └─→ (future) live readouts / alarms

  /api/obd/latest ─reads latest obd_readings─→ frontend live gauges
  /api/sensors/<id>/readings ─history─────────→ /sensors trend charts (reused)
  gps_points (raw) ──join on ms timestamp──────→ correlation overlays (engine load vs grade, MPG)
```

The only genuinely new moving part is the **Pi-side OBD reader**. The broker, ingest
subscriber, registry, and `/sensors` charts already exist and are reused.

---

## Conventions

### Topic taxonomy (fits the existing `sensors/<node>/<type>` scheme)

```
sensors/van/obd            # readings (JSON), node='van', type='obd'
sensors/van/obd/status     # LWT, retained: "online" / "offline"
```

`(node='van', type='obd')` is the stable stream key the registry is keyed on. The
ingest subscriber already wildcards `sensors/#`, so the reader is picked up with **no
Pi-side config change** (auto-discovery). `node` naming (`van` vs `engine`) is open
decision H.

### Payload

JSON whose keys are the `obd_readings` columns; `ts` canonicalized on ingest with the
existing receipt-time fallback. The reader publishes a **full current-values snapshot
per cycle** (open decision C) — fast channels fresh, slow channels last-read — so
every stored row is complete and chart/correlation queries never null-join. Unknown
keys are ignored (forward-compat), matching the platform contract.

---

## Data model

Add one wide table; reuse `sensors`. Keyed `(node, type)` like every other stream.
Columns follow the Phase 0 supported-PID findings; this is the expected starting set
for the Pentastar (nullable — a PID absent that cycle stays NULL):

```sql
CREATE TABLE obd_readings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sensor_id       INTEGER NOT NULL,           -- → sensors.id for (van, obd)
    timestamp       TEXT NOT NULL,              -- canonical ms UTC; joins gps_points
    rpm             REAL,                       -- PID 0C
    speed_kph       REAL,                       -- PID 0D (compare vs GPS speed)
    coolant_c       REAL,                       -- PID 05
    intake_c        REAL,                       -- PID 0F
    map_kpa         REAL,                       -- PID 0B (speed-density airflow input)
    engine_load_pct REAL,                       -- PID 04
    throttle_pct    REAL,                       -- PID 11
    fuel_level_pct  REAL,                       -- PID 2F (coarse)
    fuel_rate_lph   REAL,                       -- PID 5E if supported, else derived
    voltage_v       REAL,                       -- PID 42 / ATRV (chassis battery)
    run_time_s      REAL                        -- PID 1F
);
CREATE INDEX idx_obd_sensor_time ON obd_readings(sensor_id, timestamp);
CREATE INDEX idx_obd_time        ON obd_readings(timestamp);
```

Created idempotently in `init_db`'s `CREATE TABLE IF NOT EXISTS` block. DTC storage
(an `obd_dtc_events` table or reuse of the events pattern) is deferred — open
decision F.

---

## Dependencies

- **`python-OBD`** *(pending approval)* — Python ELM327/OBD-II client (pulls
  `pyserial` + `pint`). Add to `pyproject.toml` once approved. **Fallback:** raw
  ELM327 AT-commands over `pyserial` (zero new top-level dep, more code, full
  control). Approve before Phase 0's probe script, or it's written against raw serial.
- **BlueZ** (system, already on the CM5) — for BAFX BT-Classic pairing + `rfcomm`
  bind. No Python dep. (The OBDLink EX needs none of this — it's USB serial.)

No frontend deps: live gauges reuse the existing page tooling; trend charts reuse
vendored uPlot.

---

## Proposed code layout

```
sensors/
  obd.py                      # Pi-side OBD reader → MQTT publish (logger-style daemon, engine-gated)
mqttbus/
  ingest.py                   # +READING_TABLES entry for obd_readings (existing dispatch)
api/routes/
  sensors.py                  # +/api/obd/latest (or fold into the sensors routes)
tools/
  obd_probe.py                # Phase 0 throwaway: supported-PID dump + live-value log
  obd_setup.py                # (later) BT pair + rfcomm bind / USB udev-pin, mirrors gpsd_setup.py
deploy/
  sensor-obd.service          # enabled-gated, like sensor-bme680
```

Reader home is `sensors/obd.py` for platform consistency (it's a Pi-side publisher to
the `sensors/` topic namespace, and "the van as a sensor" is the project's own
framing) — `vehicle/obd.py` is the semantic alternative (open decision D).

---

## Phased plan

Each phase ships something real and de-risks the next.

### Phase 0 — De-risk with the BAFX (no purchase) — **immediate focus**

Everything downstream hinges on what this reveals. Runs on the **Pi, in the van,
engine running**, with the owned BAFX.

- [ ] **Approve `python-OBD`** (O6) — or elect the raw-serial fallback for the probe.
- [ ] **Pair the BAFX** over BT-Classic on the Pi: `bluetoothctl` scan/pair/trust the
      dongle MAC, then `sudo rfcomm bind 0 <MAC> 1` → `/dev/rfcomm0`. (rfcomm is
      semi-deprecated in BlueZ but works; its flakiness is exactly why the permanent
      reader is the USB EX.)
- [ ] **`tools/obd_probe.py`** (throwaway, `KeyboardInterrupt`→130): connect, print
      `connection.supported_commands`, then log live values for a short drive to a
      file. Settles, from the real vehicle:
      - **Does the FCA SGW allow read-only PID polling?** (any data back = yes; the
        whole reader question is unblocked.)
      - **What does the Pentastar actually report?** MAF (`10`) absent as expected?
        Is fuel rate (`5E`) present, or must we derive fuel? Fuel level (`2F`)?
      - **Real throughput** (PIDs/sec on this clone) → informs cadence + the fast/slow
        PID split.
      - **DTCs present?** (mode `03`) — for the health use case.
- [ ] **Decision gate:** finalize the PID set + cadence (open A), the fuel-rate source
      (open B), and the drain/hardware approach (O5 — EX sleep vs ignition relay,
      informed by how long the van actually sits). The OBDLink EX is on the way as the
      permanent reader regardless.

### Phase 1 — Reader + schema + ingest

- [ ] **`obd_readings`** table in `init_db` (columns per Phase 0 findings).
- [ ] **`sensors/obd.py`**: `python-OBD` reader, **engine-state-gated** polling (off →
      stop + close connection; running → fast set ~1 Hz + slow set throttled),
      auto-reconnect, heartbeat with dropped-reason counters, graceful shutdown.
      Publishes a full snapshot per cycle to `sensors/van/obd` + retained LWT on
      `.../status`. Mirrors `sensors/bme680.py`.
- [ ] **Ingest**: add the `obd_readings` entry to `READING_TABLES` (the type-dispatch
      map) — no other ingest change; auto-registration, LWT, and ms-canonicalization
      are inherited.
- [ ] **`deploy/sensor-obd.service`** + the Pi-side post-receive hook edit
      (enabled-gated restart; reader restarts only if `sensors/` changed **and**
      enabled). Reader runs disabled until the van session.
- [ ] **Verify** end-to-end: a live OBD reading travels dongle → MQTT → `obd_readings`,
      auto-registers `(van, obd)`, carries a correct ms timestamp, and survives an
      ingest restart without loss.

### Phase 2 — Logged views (covers battery/health + fuel logging)

- [ ] **`/sensors` reuse**: the registry + `/api/sensors/<id>/readings` + uPlot charts
      already render arbitrary metric columns — OBD trends (voltage, coolant, RPM,
      load, fuel level) come largely for free. Confirm the metric-column dispatch
      picks up `obd_readings`.
- [ ] Presentation call (open G): OBD alongside env sensors on `/sensors`, or a
      dedicated vehicle view.

### Phase 3 — Live gauges (O7)

- [ ] **`/api/obd/latest`** — single most-recent `obd_readings` row (fast-poll target),
      mirroring `/api/points/latest`.
- [ ] A ~1 Hz driving readout (RPM/speed/coolant/load/voltage), sidestepping the
      MQTT-over-WS blocker.

### Phase 4 — GPS correlation (the payoff)

- [ ] **MPG (O8):** integrate fuel rate over time ÷ GPS-track distance, per trip /
      annotation range.
- [ ] **Track colored by an OBD metric** — engine load / speed-delta along the route
      (generalizes the sensor-platform Phase 5 "track colored by sensor value" to OBD;
      engine-load-vs-terrain-grade is the headline overlay).
- [ ] OBD charts synced to the timeline scrubber; hover a fix → OBD values at that time.

### Phase 5 — Hardware finalize

- [ ] Swap to the **OBDLink EX** (USB): udev-pin to a stable `/dev/` path (like the
      GPS), point the reader at it — a one-line config swap (O1).
- [ ] **Ignition-switched feed** (O5) if chosen, for zero parked drain.
- [ ] DTC storage + a check-engine surface (open F) if wanted.

### Future (running list — not in scope now): Victron GX

- [ ] **House-power tier.** Venus OS ships a native MQTT broker publishing the whole
      system (battery SoC, solar yield, DC loads, charger state). **Bridge it into the
      existing mosquitto** (or point ingest at it) → same registry + per-type-table
      pattern → GPS-correlated house-power data ("solar harvest per campsite," "drain
      rate parked here"). Gated on running a network cable to the GX. *This is why
      Phase 1 builds the tier generically.*

---

## Deploy & ops impact

- **Post-receive hook**: add `sensor-obd` to the enabled-gated sensor restarts (after
  the core GPS restarts, non-fatal), restarting only if `sensors/` changed.
- **One-time `systemctl enable`** documented in CLAUDE.md when it lands; reader stays
  disabled until the dongle is paired/wired and the van session happens.
- **DB growth**: engine-gated logging bounds OBD to drive-time; ~15 cols × ~1 Hz while
  moving is modest next to 5 Hz GPS. Revisit only if it surprises.
- **Bluetooth (BAFX phase only)**: rfcomm bind must persist across reboots (systemd
  unit or `rfcomm` config) if the BAFX is used beyond Phase 0; the USB EX removes this
  entirely.

---

## CLAUDE.md impact

OBD extends the **sensor platform**, so the durable home for its architecture is
`.claude/modules/sensors.md` (where the MQTT platform already lives), with a one-line
pointer from the root Architecture section and the new process/table/endpoint noted in
the existing lists. Update as each phase lands, not retroactively.

---

## Open decisions (settle as we reach them)

| # | Decision | When | Notes |
|---|----------|------|-------|
| A | Exact PID set + cadence (fast/slow split) | Phase 0 | Driven by what the Pentastar reports + measured ELM327 throughput. |
| B | Fuel-rate source | Phase 0/4 | PID `5E` if supported; else derive from MAP/IAT/RPM speed-density + fuel trims (no MAF on the Pentastar); coarse `2F` fuel-level deltas as a floor. |
| C | Payload shape | Phase 1 | **Lean: full snapshot per cycle** (complete rows) vs. sparse per-poll (null-heavy). |
| D | Reader code home | Phase 1 | **Lean: `sensors/obd.py`** (platform consistency, "van as a sensor") vs. `vehicle/obd.py`. |
| E | Drain hardware | Phase 0 gate / Phase 5 | OBDLink EX sleep vs. ignition-switched relay feed; depends on how long the van sits. |
| F | DTC storage | Phase 5+ | Dedicated `obd_dtc_events` table vs. reuse an events pattern; deferred. |
| G | Presentation | Phase 2/3 | OBD on `/sensors` alongside env vs. a dedicated vehicle view. |
| H | `node`/`type` naming | Phase 1 | `van/obd` vs. `engine/obd` (`engine` is a listed example node). |

---

## Success criteria (go/no-go per phase)

0. **Phase 0:** the BAFX, plugged into the van with the engine running, returns
   read-only PID data through the SGW; we know the supported-PID set, real throughput,
   and whether fuel rate is native or must be derived.
1. **Phase 1:** a live OBD reading travels dongle → MQTT → `obd_readings`,
   auto-registers `(van, obd)`, carries a correct ms timestamp, and survives an ingest
   restart without loss.
2. **Phase 2:** OBD trends render on `/sensors` over van WiFi, fully offline.
3. **Phase 3:** a live driving gauge updates at ~1 Hz on the phone.
4. **Phase 4:** a trip's GPS track is colored by an OBD metric and per-trip MPG is
   computed from OBD fuel × GPS distance.
5. **Phase 5:** the OBDLink EX is the permanent reader with no code change beyond the
   device path, and parked drain is solved.

GPS logging must remain uninterrupted throughout.
