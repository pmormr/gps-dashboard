# Sensor Platform

Turns the Pi from a GPS-only logger into a centralized data-logging platform: it
still logs GPS unchanged, and additionally ingests environmental sensors (first a
BME680: temperature, humidity, pressure, gas/VOC) over an MQTT bus, into the **same**
SQLite DB so GPS↔sensor correlation is a local join.

Roadmap, locked decisions, and per-phase success criteria live in
`docs/sensor-platform-plan.md` — this file documents what has **landed**. Phases 0–1
(schema + broker + ingest + a local reader) are in; Phases 2–5 (ESP32 nodes, browser
live readouts, alarms, correlation UX) are not yet.

## Processes

GPS logging stays its own process and is **not** on the bus. New moving parts:

- **mosquitto** — local MQTT broker. tcp `:1883` (Pi-side clients) + websockets
  `:9001` (browser, MQTT-over-WS). `allow_anonymous` (trusted LAN, like the app's
  no-auth stance). Config `deploy/mosquitto.conf` → `/etc/mosquitto/conf.d/`.
- **BME680 reader** (`sensors/bme680.py`) — Pi-attached I2C sensor → MQTT publisher.
  Logger ethos: paho auto-reconnect, heartbeat, graceful shutdown. `--fake` mode
  synthesizes readings so the pipeline runs with no hardware; the real Pimoroni
  `bme680` driver is lazy-imported. Registers a retained LWT on `.../status`.
- **Ingest subscriber** (`mqttbus/ingest.py`) — the **only** writer of sensor data.
  Subscribes `sensors/#`, auto-registers sensors, canonicalizes timestamps, inserts
  per-type rows, applies LWT status. Persistent session + QoS-1 so a restart loses
  nothing. Heartbeat with a dropped-reading reason breakdown, logger-style.
- **Alarm subscriber** — Phase 4, not built yet (`mqttbus/alarms.py`).
- **Browser** — Phase 3, not built yet (MQTT-over-WS via vendored MQTT.js).

`mqttbus/client.py` is the shared paho v2 client factory (env broker host/port,
bounded reconnect backoff, optional retained LWT). `mqttbus/topics.py` is the single
source of truth for the topic taxonomy (build/parse, no broker dependency).

## Data model

Sensor tables live in `gps_history.db` alongside GPS — see `api/db.py` `init_db`:
`sensors` (registry, keyed `UNIQUE(node, type)`), `bme680_readings`, and the
`alarm_rules` / `alarm_events` tables (created now, exercised in Phase 4). All
timestamps go through `api.db.canonical_timestamp` so they join cleanly against
`gps_points`. FKs are logical/unenforced, matching the trips↔points style.

## Conventions

Topics (`mqttbus/topics.py`):

```
sensors/<node>/<type>           # readings (JSON)
sensors/<node>/<type>/status    # retained LWT: "online" / "offline"
alarms/<rule_id>                # retained alarm state (Phase 4)
```

`<node>` is a short unique name, usually the physical location (`cabin`, `engine`);
`<type>` is the sensor type (`bme680`). `(node, type)` is the registry key.
New nodes are auto-discovered — the ingest wildcard `sensors/#` needs no per-node
config.

Reading payload keys are the per-type table's columns; unknown keys are ignored
(forward-compat). **Timestamp policy:** the node stamps `ts` (NTP-synced off the Pi);
ingest keeps it when present/parseable/not-future, and falls back to receipt time
only on missing/unparseable/future-skew (`FUTURE_SKEW_SECONDS`). Older timestamps are
kept as-is so a node buffering across a Pi reboot replays with correct per-reading
times. Fallbacks are counted in the ingest heartbeat.

## Deploy & ops

Services: `deploy/{mqtt-ingest,sensor-bme680}.service` (slot into the existing
systemd + post-receive model). The hook copies the new unit files and `mosquitto.conf`
when `deploy/` changes, restarts `mqtt-ingest` when enabled, and restarts
`sensor-bme680` only when `sensors/` changes **and** the unit is enabled — so a host
without the BME680 wired never starts a crash-looping reader.

One-time Pi setup (broker install, enabling units) is in the plan's Phase 1 section.
`sensor-bme680` stays **disabled until the BME680 is physically wired** (the CM5 GPIO
I2C bus must be enabled first; currently absent). `mosquitto` + `mqtt-ingest` run now
and simply wait for messages.

**Offline:** mosquitto is a local broker; MQTT.js will be vendored (Phase 3); ESP32s
NTP-sync off the Pi. Nothing here reaches the internet at runtime. Installing
mosquitto and the `paho-mqtt`/`bme680` wheels are online prep steps (done before going
off-grid), same as tile pre-caching.
