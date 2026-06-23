# Sensor Platform

Turns the Pi from a GPS-only logger into a centralized data-logging platform: it
still logs GPS unchanged, and additionally ingests other streams over an MQTT bus into
the **same** SQLite DB, so GPS↔sensor correlation is a local join. Two streams are
live: **environmental sensors** (a BME680: temperature, humidity, pressure, gas/VOC)
and **the van itself over OBD-II** (engine RPM/speed/load, temps, fuel — see *OBD-II:
the van as a sensor* below). Adding a stream is a spec entry, not a new pipeline.

This module documents the sensor platform — what has **landed** and what's still open.
Phases 0–2 (schema + broker + ingest + the first remote ESP32 node) are in, plus a
DB-backed **`/sensors` viewer** (current values + trend charts) that delivers the
live-readout value without the blocked WS transport. Still open: *live* (push) readouts
over MQTT-over-WS, alarms, and correlation UX.

The first sensor is a **BME680 on a dedicated ESPHome ESP32-C6 node**
(`firmware/cabin-bme680.yaml`), not Pi-attached: it runs Bosch **BSEC2** on-device for
a calibrated IAQ index — closed-source C, so it can't run under MicroPython and is
fiddly on the Pi, but ESPHome's `bme68x_bsec2` component runs it cleanly and also
gives Wi-Fi/MQTT/NTP/OTA for free. The Pi-side `sensors/bme680.py` reader survives only
as a `--fake` pipeline test harness.

## Processes

GPS logging stays its own process and is **not** on the bus. New moving parts:

- **mosquitto** — local MQTT broker. tcp `:1883` (Pi-side clients) + websockets
  `:9001` (browser, MQTT-over-WS). `allow_anonymous` (trusted LAN, like the app's
  no-auth stance). Config `deploy/mosquitto.conf` → `/etc/mosquitto/conf.d/`.
- **BME680 node** (`firmware/cabin-bme680.yaml`) — the live publisher: a Seeed XIAO
  ESP32-C6 + Adafruit BME680 (I2C `0x77`), ESPHome on the esp-idf framework (required
  for the RISC-V C6). `bme68x_bsec2` runs BSEC2 (LP rate, baseline persisted to flash);
  a 30s interval lambda publishes one combined JSON reading to `sensors/cabin/bme680`,
  `utcnow()`-stamped. MQTT birth/will = the `.../status` LWT. See `firmware/README.md`.
- **BME680 reader** (`sensors/bme680.py`) — the original Pi-attached I2C publisher,
  now kept only as a `--fake` pipeline test harness (the real BME680 moved to the node
  above). Logger ethos: paho auto-reconnect, heartbeat, graceful shutdown.
- **OBD reader** (`sensors/obd_reader.py`) — the van as a sensor: a Pi-side python-OBD
  publisher on the OBDLink EX (USB `/dev/ttyUSB0`), **single serial owner**,
  **engine-gated**. It wakes on chassis voltage >13.2 V (alternator charging, debounced)
  and **closes the connection when parked** so the bus sleeps and volume is bounded to
  drive-time. Polls a mutable per-PID rate table (all 1 Hz in Phase 1), publishing a
  full-snapshot to `sensors/van/obd` with the `.../status` LWT; saturation (target vs
  actual cycle) surfaces in the heartbeat. **Trap: the file is `obd_reader.py`, not
  `obd.py`** — `obd.py` shadows the `obd` library on a script-form run (circular
  self-import → startup crash). python-OBD's engine-off ERROR/WARNING chatter is dropped
  by a logging `Filter` so a parked van stays quiet in the journal. `--fake` mode for
  desk testing. The FCA Security Gateway bypass (12+8 harness), supported-PID set, and
  the phase roadmap live in `plans/obd-platform-plan.md`.
- **Ingest subscriber** (`mqttbus/ingest.py`) — the **only** writer of sensor data.
  Subscribes `sensors/#`, auto-registers sensors, canonicalizes timestamps, inserts
  per-type rows, applies LWT status. The per-type INSERT is **column-driven** off a
  shared `READING_TABLES` spec (`api/sensor_schema.py`, imported by both ingest and the
  `/sensors` read route) — a new stream is a spec entry, not an ingest code branch.
  Persistent session + QoS-1 so a restart loses nothing. Heartbeat with a
  dropped-reading reason breakdown, logger-style.
- **Alarm subscriber** — Phase 4, not built yet (`mqttbus/alarms.py`).
- **Web app** (`api/routes/sensors.py`) — the `/sensors` page + `/api/sensors` and
  `/api/sensors/<id>/readings` JSON. Read-only, DB-backed (no MQTT): the page
  (`static/js/sensors.js`, vendored uPlot) polls every 30s for current values and
  per-metric trend charts. This is the non-live half of Phase 3; it sidesteps the
  broker websockets blocker entirely.
- **Browser (live)** — Phase 3 push path via MQTT-over-WS, **blocked:** the Pi's
  mosquitto (Debian bookworm, 2.0.11 arm64) is built *without* libwebsockets, so a
  `:9001` `protocol websockets` listener makes the broker refuse to start —
  `deploy/mosquitto.conf` stays tcp `:1883` only. Resolve by rebuilding mosquitto with
  libwebsockets, finding a WS-enabled package, or bridging MQTT→browser through Flask
  (SSE / server-side WS). The DB-backed viewer above sidesteps it and covers most of the
  need.

`mqttbus/client.py` is the shared paho v2 client factory (env broker host/port,
bounded reconnect backoff, optional retained LWT). `mqttbus/topics.py` is the single
source of truth for the topic taxonomy (build/parse, no broker dependency).

## Data model

Sensor tables live in `gps_history.db` alongside GPS — see `api/db.py` `init_db`:
`sensors` (registry, keyed `UNIQUE(node, type)`), `bme680_readings`, `obd_readings`,
and the `alarm_rules` / `alarm_events` tables (created now, exercised in Phase 4). All
timestamps go through `api.db.canonical_timestamp` so they join cleanly against
`gps_points`. FKs are logical/unenforced, matching the trips↔points style. Each
per-type table's columns are declared once in `api/sensor_schema.py` `READING_TABLES`,
which drives both the ingest INSERT and the read route.

`bme680_readings` carries the BSEC2 outputs alongside the raw channels: `temp_c`,
`humidity_pct`, `pressure_hpa`, `gas_ohms` (raw resistance) plus `iaq`, `iaq_accuracy`
(0–3; IAQ is meaningless until ≥1), `co2_equivalent`, `breath_voc_equivalent`. The IAQ
columns were added by an idempotent `ALTER TABLE` in `db.py` `migrate()` (the Pi's
table predates them); absent payload keys store NULL.

`obd_readings` is the wide table for the van stream — 18 captured PIDs (RPM, speed,
engine + absolute load, throttle, coolant/intake/ambient temps, MAP, barometric, fuel
level, voltage, run-time, short/long fuel trims both banks, commanded equivalence
ratio) plus a nullable `fuel_rate_lph`. Fuel rate is **NULL by design**: the Pentastar
is speed-density (no MAF `0110`) and exposes no native fuel-rate `015E`, so it's derived
from absolute load + RPM + λ in Phase 4 (validated against a tank-to-tank fill-up). The
column set is the `READING_TABLES` spec, not hand-written SQL.

## Conventions

Topics (`mqttbus/topics.py`):

```
sensors/<node>/<type>           # readings (JSON)
sensors/<node>/<type>/status    # retained LWT: "online" / "offline"
alarms/<rule_id>                # retained alarm state (Phase 4)
```

`<node>` is a short unique name, usually a physical location (`cabin`) or subsystem
(`van`); `<type>` is the stream type (`bme680`, `obd`). `(node, type)` is the registry
key. New nodes are auto-discovered — the ingest wildcard `sensors/#` needs no per-node
config.

Reading payload keys are the per-type table's columns; unknown keys are ignored
(forward-compat). **Timestamp policy:** the node stamps `ts` (NTP-synced off the Pi);
ingest keeps it when present/parseable/not-future, and falls back to receipt time
only on missing/unparseable/future-skew (`FUTURE_SKEW_SECONDS`). Older timestamps are
kept as-is so a node buffering across a Pi reboot replays with correct per-reading
times. Fallbacks are counted in the ingest heartbeat.

## Deploy & ops

Services: `deploy/{mqtt-ingest,sensor-bme680,sensor-obd}.service` (slot into the
existing systemd + post-receive model). The hook copies the new unit files and
`mosquitto.conf` when `deploy/` changes, restarts `mqtt-ingest` when enabled, and
restarts `sensor-bme680` / `sensor-obd` only when `sensors/` changes **and** the unit
is enabled — so a host without that hardware never starts a crash-looping reader.

One-time Pi setup (broker install, enabling units) is in the plan's Phase 1 section.
`mosquitto` + `mqtt-ingest` run on the Pi and ingest whatever publishes. The
`sensor-bme680` service stays **disabled** — the BME680 moved to the ESPHome node, so
the Pi-attached reader has no hardware (and the CM5 GPIO I2C bus is still off).
`sensor-obd` **is enabled** (node `van`, `/dev/ttyUSB0`): it owns the OBDLink EX and
self-gates on engine state, so it idles cleanly when the van is parked.

The ESP32 node is flashed from a dev host, not the Pi:
`uv tool run esphome run firmware/cabin-bme680.yaml` (first flash over USB, OTA after;
copy `firmware/secrets.yaml.example` → `secrets.yaml` first). See `firmware/README.md`.

**Offline:** mosquitto is a local broker; MQTT.js will be vendored (Phase 3); the ESP32
NTP-syncs off the Pi and talks only to the local broker. Nothing here reaches the
internet at runtime. Installing mosquitto/`paho-mqtt` and **building the ESPHome
firmware** (ESP-IDF toolchain + BSEC2) are online prep steps, same as tile pre-caching.
