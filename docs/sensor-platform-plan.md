# Sensor Platform Plan

## Context

The app today logs **one** stream — GPS — into a GPS-specific table, written by a
single dedicated process (`logger/gps_logger.py`). The next step turns this Pi
into a **centralized data-logging platform**: it keeps logging GPS, but also
ingests environmental sensors (temperature, humidity, barometric pressure, gas/VOC
air quality, …) from two kinds of source:

- **Locally attached** sensors on the Pi (I2C/SPI/serial) — first up: a **BME680**.
- **Remote** sensors on **ESP32** nodes scattered around the van, each running a
  lightweight program that publishes readings over the van LAN.

The payoff is **correlation with GPS on long road trips** — air quality, cabin vs.
exterior climate, pressure — plotted along the route and over time. Plus **live
readouts** and **threshold alarms** (a van you sleep in wants a CO/temperature
alarm).

This is a multi-phase effort like the vector-tiles migration; treat this doc as the
durable, living plan (check items off as they land, record decisions inline).

---

## Confirmed decisions (locked before writing this doc)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Data model | **Per-sensor-type tables + a `sensors` registry** | A handful of sensor *types*, not hundreds. Typed columns keep correlation queries and charts simple; GPS stays its own table. Cost is a migration per new *type*, not per node. |
| 2 | Transport / bus | **MQTT from day one** (mosquitto on the Pi) | Several nodes + live + alarms is past the break-even. Zero-config node discovery, free liveness via LWT, queuing across Pi reboots, and fan-out to multiple consumers are architectural — not cleanly retrofittable onto HTTP push. |
| 3 | Live transport to browser | **Browser-direct MQTT-over-WebSockets** (mosquitto WS listener + vendored MQTT.js) | Least Pi-side code; browser gets retained "current conditions" instantly and the live stream over one subscription; the same connection can drive alarm banners. |
| 4 | Registry population | **Auto-discover** (upsert on first message + LWT), friendly name/location editable after | Matches MQTT's zero-config spirit; no manual registration step. |
| 5 | Timestamps | **Node-stamped (NTP off the Pi), Pi falls back to receipt-time if missing/implausible** | Makes node-side buffering across a Pi reboot correct (receipt-stamping alone collapses replayed readings onto one wrong time). Viable because the Pi is a stratum-1 NTP server. |

---

## Constraints carried from the project

- **Offline-first.** Everything runs on the van LAN with **zero internet**.
  mosquitto is a local broker; MQTT.js is **vendored** into `static/vendor/` (no
  CDN at runtime, same rule as Leaflet/MapLibre). ESP32s NTP-sync off the Pi, not
  the internet.
- **Mobile-first.** Primary client is a phone browser over van WiFi. Live readouts
  and charts must be usable on a phone.
- **GPS logging is sacred.** `gps_logger` stays exactly as-is (working, stall- and
  freeze-hardened). It is **not** moved onto the bus in this effort. New sensor
  readers must never be able to stall GPS.
- **Deploy model.** Two systemd services today (`gps-logger`, `gps-dashboard`),
  managed via a bare repo + post-receive hook that `uv sync`s, always restarts
  `gps-dashboard`, and restarts `gps-logger` only if `logger/` changed. New
  services slot into this model (see "Deploy & ops impact").
- **Persistent assets survive deploys** (DB, tile caches, PMTiles). Sensor data
  lives in the **same SQLite DB** (`/mnt/nvme/data/gps_history.db`) so GPS↔sensor
  correlation is a local join, not a cross-store problem.
- **Reuse what exists.** All timestamps go through `api.db.canonical_timestamp`
  (whole-second UTC) so sensor readings join cleanly against `gps_points`. Daemons
  follow the logger's ethos: auto-reconnect, periodic heartbeat with
  dropped-reading reason counters, graceful shutdown.

---

## Architecture

```
  [ESP32 nodes]──┐                          ┌─→ ingest subscriber ──→ SQLite (per-type tables)
  (BME680, …)    │                          │   auto-registers sensors, applies LWT status
                 ├─→ mosquitto broker ───────┼─→ alarm subscriber ───→ alarm_events + alarms/* (retained)
  [Pi BME680     │   :1883 tcp  :9001 ws      │
   reader]───────┘   sensors/#, LWT, retained └─→ browser (MQTT-over-WS) ─→ live readouts + alarm banner

  gps_logger ──→ SQLite (gps_points, unchanged — not on the bus)
```

Five new moving parts: **mosquitto**, a **Pi BME680 reader** (publisher), an
**ingest subscriber** (the system-of-record writer), an **alarm subscriber**, and
the **browser** as a fourth broker client over WebSockets. SQLite is still the
archive — MQTT routes in-flight messages, it does not store history.

---

## Conventions

### Topic taxonomy

```
sensors/<node>/<type>           # readings (JSON payload)
sensors/<node>/<type>/status    # LWT, retained: "online" / "offline"
alarms/<rule_id>                # alarm state, retained: "active" / "cleared" (published by alarm subscriber)
```

- `<node>` is a short **unique** name, usually equal to its physical location
  (`cabin`, `exterior`, `engine`, `bedroom`); disambiguate co-located sensors as
  `cabin-co`, `cabin-air`. `<type>` is the sensor type (`bme680`, …). The
  `(node, type)` pair is the stable stream key the registry is keyed on.
- The ingest subscriber uses wildcard `sensors/#`, so a brand-new node is picked up
  with **no Pi-side config change** (Decision #4).

### Payload (per-type JSON)

```json
{
  "ts": "2026-06-09T14:55:55Z",   // optional; whole-second UTC. Pi falls back to receipt time if absent/implausible.
  "temp_c": 22.4,
  "humidity_pct": 41.2,
  "pressure_hpa": 1013.2,
  "gas_ohms": 120000
}
```

- `ts` is canonicalized through `canonical_timestamp` on ingest. The fallback to
  receipt time fires when `ts` is **missing**, **unparseable**, or more than
  `FUTURE_SKEW_SECONDS` (60s) **in the future** — each counted separately in the
  ingest heartbeat (`ts_missing` / `ts_bad` / `ts_future`). Timestamps *older* than
  receipt are **kept as-is**, not rejected: that is the whole point of Decision #5
  (a node buffering across a Pi reboot must replay with its own per-reading times,
  not collapse onto receipt time). So "implausible" means future/garbage clocks,
  not staleness. (This refines the earlier "±N s window" sketch, which would have
  wrongly discarded legitimately buffered readings.)
- Keys are the per-type table's columns. Unknown keys are ignored (forward-compat).

---

## Data model

Add to `api/db.py` `init_db`/`migrate` (same DB). `gps_points`/`trips`/`marks`
unchanged. `sensor_id` is a logical reference (the project runs SQLite with FKs
unenforced, matching the existing trips↔points "no foreign keys" style).

```sql
CREATE TABLE sensors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node        TEXT NOT NULL,            -- topic <node> segment
    type        TEXT NOT NULL,            -- 'bme680', ...
    location    TEXT,                     -- friendly, editable
    description TEXT DEFAULT '',
    first_seen  TEXT NOT NULL,
    last_seen   TEXT,
    status      TEXT DEFAULT 'unknown',   -- online/offline/unknown (LWT)
    UNIQUE(node, type)
);

CREATE TABLE bme680_readings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sensor_id     INTEGER NOT NULL,
    timestamp     TEXT NOT NULL,
    temp_c        REAL,
    humidity_pct  REAL,
    pressure_hpa  REAL,
    gas_ohms      REAL                    -- raw gas resistance; IAQ derived later (see note)
);
CREATE INDEX idx_bme680_sensor_time ON bme680_readings(sensor_id, timestamp);
CREATE INDEX idx_bme680_time        ON bme680_readings(timestamp);

CREATE TABLE alarm_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sensor_id   INTEGER,                  -- nullable = applies by type/metric broadly
    metric      TEXT NOT NULL,            -- 'temp_c', 'gas_ohms', ...
    min_value   REAL,                     -- nullable
    max_value   REAL,                     -- nullable
    hysteresis  REAL DEFAULT 0,           -- deadband to stop flapping
    enabled     INTEGER DEFAULT 1,
    name        TEXT
);

CREATE TABLE alarm_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id     INTEGER NOT NULL,
    state       TEXT NOT NULL,            -- 'active' / 'cleared'
    value       REAL,
    timestamp   TEXT NOT NULL
);
```

> **BME680 air quality note.** The open Python drivers expose **raw gas
> resistance** (Ω), not an IAQ index. A real IAQ number needs Bosch's **BSEC** blob
> (closed-source) or a heuristic, and the gas heater needs warm-up/burn-in, so the
> reading is *relative*. Store raw `gas_ohms` now; derive an IAQ index later as a
> separate decision. Don't block the pipeline on it.

---

## Dependencies (approved)

- **mosquitto** — broker (apt; ships its own systemd unit). Config via
  `deploy/mosquitto.conf`.
- **paho-mqtt** — Python client for the ingest + alarm subscribers and the Pi
  reader. Add to `pyproject.toml`.
- **BME680 driver** — Python I2C lib for the Pi reader (e.g. `bme680` /
  `adafruit-circuitpython-bme680` / raw `smbus2`). Confirm which at Phase 1.
- **MQTT.js** — frontend, **vendored** into `static/vendor/mqtt/` for browser-direct
  WS (Decision #3).
- **ESP32 firmware** is C++/Arduino, ESP-IDF, or MicroPython — outside this Python
  repo. Needs a home: a `firmware/` subdir here vs. a separate repo (decide at
  Phase 2).

---

## Proposed code layout

Adjustable, but a concrete starting point:

```
sensors/                      # Pi-side sensor readers (publishers)
  bme680.py                   # I2C BME680 → MQTT publish (logger-style daemon)
mqttbus/                      # broker-side consumers + shared MQTT helpers
  topics.py                   # topic taxonomy build/parse helpers (no broker dep)
  client.py                   # connect/reconnect/LWT boilerplate
  ingest.py                   # subscribe sensors/# → SQLite (system of record)
  alarms.py                   # subscribe sensors/# → evaluate rules → alarm_events + alarms/*
api/routes/sensors.py         # REST: registry, history, current values, alarm CRUD
static/js/sensors.js          # live readouts (MQTT.js), charts, alarm banner
static/vendor/mqtt/           # vendored MQTT.js
deploy/
  mosquitto.conf
  sensor-bme680.service
  mqtt-ingest.service
  mqtt-alarms.service
firmware/                     # (Phase 2; or separate repo) ESP32 node program
```

---

## Phased plan

Each phase ships something real and de-risks the next.

### Phase 0 — Schema + conventions (no hardware) — **DONE**
- [x] Add `sensors`, `bme680_readings`, `alarm_rules`, `alarm_events` to
      `init_db`. All four created now (alarm tables sit empty until Phase 4).
      Creation is idempotent via `init_db`'s existing `CREATE TABLE IF NOT EXISTS`,
      which runs on every app/logger startup — no separate `migrate` block needed.
- [x] Lock the topic taxonomy + payload schema + timestamp policy in this doc
      (above) and add `mqttbus/topics.py` helpers (build/parse topic strings).
- [x] Reuse `canonical_timestamp` for all sensor timestamps (deferred to ingest in
      Phase 1; Phase 0 adds no new timestamp code).
- [x] No broker, no hardware yet — this is the data-model foundation.

> **Decision (Phase 0): package renamed `mqtt/` → `mqttbus/`.** A top-level
> `mqtt/` package risks shadowing the `paho-mqtt` import; `mqttbus/` is
> unambiguous. The proposed `client.py`/`ingest.py`/`alarms.py` modules land under
> `mqttbus/` too. Layout block below updated to match.

### Phase 1 — Broker + ingest, one local sensor end-to-end — **code DONE, Pi deploy pending**
- [x] Install/configure **mosquitto**: `deploy/mosquitto.conf` with a tcp listener
      (`:1883`), `allow_anonymous true` (trusted LAN); persistence inherited from the
      package default. **WS `:9001` deferred** — the Pi's build lacks libwebsockets
      (Phase 3 blocker F). Installed + enabled on the Pi via `conf.d/`.
- [x] **Pi BME680 reader** (`sensors/bme680.py`): reads I2C on an interval, publishes
      to `sensors/<node>/bme680`, registers a retained LWT on `.../status`. Logger
      ethos: paho auto-reconnect, heartbeat, graceful shutdown. **`--fake` mode**
      synthesizes readings so the pipeline is decoupled from soldering. Driver
      (Decision A) = Pimoroni `bme680`, lazy-imported so `--fake` needs no I2C lib.
- [x] **Ingest subscriber** (`mqttbus/ingest.py`): subscribes `sensors/#`,
      auto-upserts the `sensors` row (first_seen/last_seen/status), canonicalizes
      `ts` with receipt fallback, inserts into `bme680_readings`, applies `.../status`
      (LWT) to `sensors.status`. Persistent session (clean_session=False) + QoS-1 so
      messages queue across an ingest restart with no loss. Retained reading copies
      are skipped on resubscribe to avoid double-insert.
- [x] `deploy/sensor-bme680.service` + `deploy/mqtt-ingest.service` written.
- [ ] Wire into the **post-receive hook** on the Pi (see "Deploy & ops impact") —
      not tracked in this repo; applied directly on the Pi.
- [x] **Verified end-to-end in dev** (sandboxed mosquitto on high ports): fake
      reading → MQTT → SQLite; row lands with a sane timestamp; sensor auto-registers;
      `online`→`offline` via LWT on ungraceful kill; **no loss across an ingest
      restart** (queued QoS-1 message redelivered). Success criteria 1 (+ part of 2)
      met before hardware.

> **Decisions settled at Phase 1:** A — driver = Pimoroni `bme680`. D — broker auth
> = **anonymous** (trusted LAN, matches the app's no-auth stance). Deps added:
> `paho-mqtt` 2.x (`CallbackAPIVersion.VERSION2`) and `bme680`.
>
> **Pi one-time setup (still to do):**
> ```bash
> sudo apt-get install -y mosquitto
> sudo cp deploy/mosquitto.conf /etc/mosquitto/conf.d/gps-sensors.conf
> sudo mkdir -p /mnt/nvme/data/mosquitto
> sudo systemctl enable --now mosquitto
> sudo cp deploy/mqtt-ingest.service deploy/sensor-bme680.service /etc/systemd/system/
> sudo systemctl daemon-reload
> sudo systemctl enable --now mqtt-ingest sensor-bme680
> ```
> Plus the post-receive hook edit below.

### Phase 2 — First remote node (ESP32)
- [ ] Decide firmware home (`firmware/` vs. separate repo) and stack
      (Arduino/ESP-IDF/MicroPython).
- [ ] Lightweight node program: read BME680, **NTP-sync off the Pi**
      (`192.168.42.178`), publish to `sensors/<node>/bme680` with `ts`, register
      LWT.
- [ ] **Verify zero-config discovery:** plug the node in; it appears in the
      `sensors` registry and starts logging with **no Pi-side change**. Kill its
      power; confirm `status` flips to `offline` via LWT.

### Phase 3 — Live readouts (browser-direct MQTT-over-WS)

> ⚠️ **Blocker found at Phase 1 deploy:** the Pi's mosquitto (Debian bookworm,
> 2.0.11 arm64) is built **without libwebsockets** — a `protocol websockets`
> listener makes the broker refuse to start, so the `:9001` WS listener is **not**
> in `deploy/mosquitto.conf` yet (tcp `:1883` only). Browser-direct MQTT-over-WS
> (Decision #3) can't work until this is resolved. Options to settle before Phase 3
> (Open decision F): (a) rebuild mosquitto with libwebsockets, (b) find a Debian
> backport / alternative package with WS, or (c) drop browser-direct WS and bridge
> MQTT→browser through the Flask app (SSE or a server-side WS), which keeps the
> broker tcp-only at the cost of more Pi-side code than Decision #3 assumed.

- [ ] Resolve the websockets blocker above, then add the `:9001` WS listener.
- [ ] Vendor **MQTT.js** into `static/vendor/mqtt/`.
- [ ] `static/js/sensors.js`: connect to mosquitto WS (`:9001`), subscribe
      `sensors/#`, render a **current-values panel** (seeded instantly by retained
      messages) + a **live chart** updating in real time.
- [ ] Mobile layout pass (phone is the primary client).
- [ ] `GET /api/sensors` (registry) + `GET /api/sensors/<id>/readings?start=&end=`
      (history) for non-live views.

### Phase 4 — Alarms
- [ ] **Alarm subscriber** (`mqtt/alarms.py`): subscribe `sensors/#`, evaluate
      `alarm_rules` per reading with **hysteresis**; on state change write
      `alarm_events` and publish retained `alarms/<rule_id>`.
- [ ] `deploy/mqtt-alarms.service`.
- [ ] REST CRUD for rules (`api/routes/sensors.py`), dashboard rule editor.
- [ ] Browser **alarm banner** driven by subscribing to `alarms/#` over the same WS
      connection (retained → instant current state).

### Phase 5 — Correlation UX (the payoff)
- [ ] Sensor-data-over-time-range API aligned to a trip's bounds; join to
      `gps_points` by nearest timestamp (or interpolate).
- [ ] **Track colored by a sensor value** — the GPS polyline drawn as a heatmap of
      temperature / humidity / VOC along the route.
- [ ] Time-series charts under the map, **synced to the timeline scrubber**.
- [ ] Hover a map point → show all sensor values at that time.

---

## Deploy & ops impact

- **Post-receive hook.** Today it always restarts `gps-dashboard` and restarts
  `gps-logger` only if `logger/` changed. Extend it to restart `mqtt-ingest` and
  `mqtt-alarms` (cheap, no data gap) and to restart the sensor readers
  (`sensor-bme680`) **only if `sensors/` changed** — same gap-avoidance logic as
  GPS. mosquitto restarts only when `deploy/mosquitto.conf` changes.
- **New services** (`mosquitto`, `sensor-bme680`, `mqtt-ingest`, `mqtt-alarms`)
  install like the existing units; document the one-time `systemctl enable` in
  CLAUDE.md.
- **Firewall:** the mosquitto WS port (`:9001`) and tcp port (`:1883`) must be
  reachable on the van LAN (ESP32s + phone browser).
- **DB growth.** Several sensors at a few-second cadence add rows faster than GPS.
  Revisit retention/downsampling once real volume is known (out of scope now;
  flag if `gps_history.db` growth becomes a concern on the NVMe).

---

## CLAUDE.md impact

This roughly doubles the architecture surface. Per the project's CLAUDE.md
philosophy (split at ~100–200 lines), the sensor platform should land as a
**module file** (`.claude/modules/sensors.md`) referenced from the root, not
inflate the root Architecture section. Update docs as each phase lands, not
retroactively.

---

## Open decisions (settle as we reach them)

| # | Decision | When | Notes |
|---|----------|------|-------|
| A | BME680 Python driver choice | Phase 1 | Pimoroni `bme680` vs Adafruit CircuitPython vs raw `smbus2`. |
| B | IAQ index source | later | Raw `gas_ohms` now; BSEC blob vs. heuristic vs. leave raw. |
| C | ESP32 firmware home + stack | Phase 2 | `firmware/` subdir vs. separate repo; Arduino/ESP-IDF/MicroPython. |
| D | Broker auth | Phase 1 | **Settled: anonymous** (trusted LAN, matches the app's no-auth stance). |
| E | Retention / downsampling | post-Phase 5 | Only if DB growth warrants it. |
| F | Websockets transport for the browser | Phase 3 | Debian's mosquitto lacks libwebsockets (found at Phase 1 deploy). Rebuild with WS, find a backport, or bridge MQTT→browser via Flask. See Phase 3 blocker note. |

---

## Success criteria (go/no-go per phase)

1. **Phase 1:** a local BME680 reading travels sensor → MQTT → SQLite, auto-registers,
   carries a correct timestamp, and survives a `gps-dashboard`/ingest restart
   without loss.
2. **Phase 2:** a remote ESP32 node is discovered with zero Pi-side config and its
   loss is detected via LWT.
3. **Phase 3:** current values + a live chart render on the **actual phone** over
   van WiFi, fully offline.
4. **Phase 4:** a threshold breach raises an alarm (banner + `alarm_events`) and
   clears with hysteresis (no flapping).
5. **Phase 5:** a trip's GPS track is colored by a sensor value and charts sync to
   the scrubber.

GPS logging must remain uninterrupted throughout.
</content>
</invoke>
