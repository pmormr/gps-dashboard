# Motion / IMU Plan

## Context

The app logs GPS and (now) environmental sensors. This effort adds a third sensing
domain: **vehicle motion** — heading, tilt/orientation, and high-rate **vibration for
road-quality estimation** — from a single 9-DOF IMU (**InvenSense ICM-20948**:
accel + gyro + magnetometer) on a chassis-mounted ESP32 node.

Two motivations, one part:

- **Heading while parked.** GPS already gives true heading via `track`
  (course-over-ground), but COG is undefined at standstill and very low speed —
  exactly when the skyplot van glyph falls back to the observer dot. A magnetometer
  fills that gap.
- **Road quality.** A high-rate accelerometer judges ride roughness, correlated to
  GPS position and speed — a "how rough was this road" layer over the route.

The defining design fact: this introduces **two data-rate classes**. Heading/tilt are
human-rate (1 Hz, fits the existing sensor platform unchanged). Vibration lives in
**0.5–80 Hz** (ISO 2631 whole-body), so the accelerometer must be *sampled* at
≥160 Hz — realistically 200–500 Hz, triaxial. Raw vibration is never stored or
transported; features are **extracted at the edge** and published at ~1 Hz. This is
the same instinct the codebase already has (motion-gated logger, derived sparse
processed tier, size-aware decimation): high-rate raw at the edge → derived low-rate
features into the DB → correlated with GPS.

Treat this doc as the durable, living plan — check items off as they land, record
decisions inline.

---

## Confirmed decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Sensor | **ICM-20948** (9-DOF, single chip) | One part covers heading, tilt, g-events, and high-rate vibration. Ordered. |
| 2 | Fusion | **DIY** (no onboard absolute-orientation engine like the BNO0xx) | The ICM-20948's DMP can emit a 9-axis fusion quaternion (SparkFun driver exposes it); alternative is a software filter (Madgwick/Mahony) on raw. Vibration reads **raw high-rate accel** regardless, bypassing the DMP. |
| 3 | Interface | **SPI** (not I2C) | Up to 7 MHz gives FIFO-drain headroom at 500 Hz × 9-DOF. I2C @ 400 kHz can do it but is tight. Breakouts expose both. |
| 4 | Transport / data model | **Reuse the MQTT sensor platform** — new `<type>`(s) under `sensors/<node>/…`, ingested into a new per-type table | The slow channel is just another sensor type; the wildcard `sensors/#` auto-registers it. No new transport. |
| 5 | Rate split | **Edge feature-extraction; publish ~1 Hz** | Raw 200–500 Hz triaxial as per-sample MQTT/SQLite rows melts the pipeline (~gigabytes/day). Store features, not samples. |
| 6 | Heading calibration | **Auto-calibrate magnetic heading against GPS `track` while moving** | A van is a steel box — severe hard/soft-iron + dynamic (alternator/load) distortion. GPS COG is free ground truth above a speed threshold; coast on the learned offset while parked. Sidesteps most manual calibration. |

---

## Open decisions (deferred — refine when we continue)

| # | Decision | Options | Notes |
|---|----------|---------|-------|
| A | **MCU board** | ESP32-**S3** vs start on a spare **C6** and defer | C6 (single-core RISC-V) is fine for bring-up, the slow heading channel, and **RMS/VDV** vibration features. **S3** (Xtensa + ESP-DSP) earns its keep only for the **FFT band-energy** path. Lean: start on a C6, decide S3 when/if we hit FFT. |
| B | **Primary vibration goal** | Live roughness **map layer** vs raw **event captures** | Map layer steers Phases 4–5 (processor + render); event captures steer Phase 3 (snippets). Not exclusive — which leads. |

---

## Constraints carried from the project

- **Offline-first.** New node talks only to the local broker and NTP-syncs off the
  Pi (stratum-1 GPS+PPS), same as the cabin BME680 node. Building the firmware is an
  online prep step; runtime is fully offline.
- **GPS logging is sacred.** Untouched; not on the bus.
- **Same SQLite DB.** Motion data joins `gps_points` locally (speed + position) via
  `api.db.canonical_timestamp`. Time alignment is NTP-over-WiFi (~ms–tens of ms); at
  60 mph, 30 ms ≈ 0.8 m — adequate for road features.
- **Deploy model.** New service(s) slot into the bare-repo + post-receive hook model.
- **Mounting.** The node is rigidly coupled to the body with a known axis alignment
  (z = vertical), magnetically as clean as practical (away from current-carrying
  wire, speakers, large steel). This is a *separate* mounting concern from the cabin
  air-quality node — almost certainly its own physical node.

---

## Architecture

```
  [ICM-20948 on ESP32 node]                    ┌─→ ingest ──→ SQLite (imu_readings, imu_events)
   SPI, raw FIFO @ 200–500 Hz                   │
        │                                       │
        ├─ slow:  1 Hz heading/tilt/g  ─────────┤
        │         sensors/<node>/imu            │
        ├─ fast:  edge features (RMS/VDV/…)  ────┼─→ processor ─→ road_segments (speed-normalized roughness)
        │         1 Hz feature vector           │   (joins IMU features × GPS speed/position)
        └─ event: threshold raw snippet  ───────┘
                  → imu_events                       └─→ frontend: /sensors trends + roughness map layer on /
```

ESPHome note: ESPHome's sensor model is **poll-based at human rates** — fine for the
slow channel, but it has no clean path for owning an IMU FIFO at 500 Hz + windowed
DSP. The vibration node therefore runs **dedicated ESP-IDF firmware**, not ESPHome.
(Verify current ESPHome FIFO/DSP support before treating this as final.) Since we want
high-rate vibration anyway, putting *all* motion sensing on one custom-firmware node is
cleaner than splitting an ESPHome heading node from a custom vibration node.

---

## Road-quality method (target, not yet built)

The named metric is **IRI** (International Roughness Index), but true IRI needs a
quarter-car road *profile* — hard from raw acceleration. The pragmatic, well-precedented
target (smartphone road-roughness literature) is a **speed-normalized vertical-acceleration
roughness score**: ISO-2631-weighted RMS / VDV (vibration dose value) over short windows,
binned to GPS segments. Not calibrated IRI, but a consistent, comparable index. Speed
normalization is essential — the same bump reads differently at 20 vs 60 mph — and we
already log speed, so the normalization join is local. The gyro separates body *rotation*
(pitch over bumps, roll in corners) from *translation*, cleaning the estimate.

---

## Phases

Sequenced so the easy win lands first and the MQTT path is proven on a slow sensor
before the hard real-time DSP — same incremental, tiered instinct as the denoise work.
**Phase 1 alone satisfies the original compass goal**, so value is banked even if the
vibration DSP gets fiddly.

- **Phase 0 — Bench bring-up.** ESP32 + ICM-20948 over SPI; confirm WHO_AM_I; stream
  raw accel/gyro/mag over serial; validate axes (z = vertical) and ranges. No MQTT.
- **Phase 1 — Slow channel → platform (delivers the compass).** 1 Hz heading/tilt/g to
  `sensors/<node>/imu`; add `imu_readings` table + ingest branch; show on `/sensors`;
  feed the skyplot parked-state glyph. Heading = tilt-compensated magnetometer + GPS-
  `track` auto-cal to start; gyro fusion later.
- **Phase 2 — High-rate vibration features.** Dedicated FIFO task at 200–500 Hz;
  windowed RMS / peak / ISO-2631 VDV (FFT band energies if on S3); published as a 1 Hz
  feature vector.
- **Phase 3 — Event snippets.** Threshold-triggered raw waveform capture stored as
  events — mirrors `track_events`.
- **Phase 4 — Processor stage.** Join IMU features × GPS speed/position → speed-
  normalized roughness per segment; rebuildable tier, same ethos as denoise.
- **Phase 5 — Map layer.** Color the track by roughness on `/`. The payoff.

---

## Codebase touchpoints (anticipated)

- **`api/db.py`** — `imu_readings` (1 Hz: heading/tilt/g + vibration features) and an
  `imu_events` snippet table mirroring `track_events`; later a derived `road_segments`
  roughness table. Heading is **circular** — store degrees, don't average naively.
- **`mqttbus/ingest.py`** — per-type insert branch(es) for the new topic(s); wildcard
  auto-registers them.
- **`processor/`** — a new stage joining IMU features × GPS speed/position → roughness
  per segment (rebuildable from raw, same ethos as denoise).
- **Frontend** — `/sensors` for heading/vibration trends (heading wants a polar/compass
  widget, not a uPlot line chart); a roughness **map layer** on `/`.
- **`/skyplot`** — swap the parked-state observer-dot fallback to the compass heading;
  the moving case keeps using `track`.
- **Firmware** — new **ESP-IDF** motion node (not ESPHome).
</content>
</invoke>
