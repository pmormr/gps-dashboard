# Sensor Platform

Turns the Pi from a GPS-only logger into a centralized data-logging platform: it
still logs GPS unchanged, and additionally ingests other streams over an MQTT bus into
the **same** SQLite DB, so GPS↔sensor correlation is a local join. Six stream types are
live: **environmental sensors** (a BME680: temperature, humidity, pressure, gas/VOC),
**the van itself over OBD-II** (engine RPM/speed/load, temps, fuel — see *OBD-II: the
van as a sensor* below), **house power over Victron** (a Venus OS GX: battery /
solar / inverter / AC + DC, bridged from its own MQTT broker), **the Pi host
itself** (CPU temp, load, memory, disk, uptime, throttle flags), **the van-edge
router** (OpenWrt/HaLow health over SSH-poll), and **the recording fleet** (Dahua
NVR + 4 cameras over HTTP CGI + RPC2 — one reader, five node streams). Adding a
stream is a spec entry, not a new pipeline.

This module documents the sensor platform — what has **landed** and what's still open.
The foundation (schema + broker + ingest + the first remote ESP32 node) is in, plus a
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
  The node sends temp/RH only; ingest derives `dew_point_c` + `abs_humidity_gm3`
  into the same row (`common/humidity.py`, Magnus) so the physics never lives in
  firmware and the columns bucket like any metric in `/api/sensors/series`.
- **BME680 reader** (`sensors/bme680.py`) — the original Pi-attached I2C publisher,
  now kept only as a `--fake` pipeline test harness (the real BME680 moved to the node
  above). Logger ethos: paho auto-reconnect, heartbeat, graceful shutdown.
- **OBD reader** (`sensors/obd_reader.py`) — the van as a sensor: a Pi-side python-OBD
  publisher on the OBDLink EX (USB `/dev/ttyUSB0`), **single serial owner**,
  **engine-gated**. It wakes on chassis voltage >13.2 V (alternator charging, debounced)
  and **closes the connection when parked** so the bus sleeps and volume is bounded to
  drive-time. Polls a mutable per-PID rate table (all 1 Hz in Phase 1), publishing a
  full-snapshot to `sensors/van/obd` with the `.../status` LWT; saturation (target vs
  actual cycle) surfaces in the heartbeat. Each parked wake also **classifies the
  physical link** and publishes it retained on `.../status` (transitions only, loop-owned
  à la Victron): `no_adapter` (serial/ELM unreachable — USB unplugged), `no_car` (ELM
  answers but reads <6 V — adapter out of the OBD socket; python-OBD's own `AT RV` check),
  `online` (socket powered). Ingest lands it in `sensors.status`, `/api/status` serves it
  as `obd_link`, and the Home Van card renders the faults — so an unplugged cable is
  never mistaken for "engine off". **Trap: the file is `obd_reader.py`, not
  `obd.py`** — `obd.py` shadows the `obd` library on a script-form run (circular
  self-import → startup crash). python-OBD's probe-time ERROR/WARNING chatter (engine
  off, port missing, socket disconnected) is dropped by a logging `Filter` so a parked
  van stays quiet in the journal — the heartbeat's `link=`/`voltage=` carry the signal. `--fake` mode for
  desk testing. **Bus access:** the OBD port's diagnostic CAN is physically isolated by
  the FCA Security Gateway — two adapters (BAFX clone, then the genuine EX) `CAN ERROR`ed
  through it, and OBDLink's AutoAuth unlock is online + app-locked, unfit for a headless
  offline reader — so a **12+8 SGW-bypass harness** (fitted 2026-06-22) bridges the
  diagnostic CAN around the gateway. The bus is ISO 15765-4 CAN **29-bit**/500k
  (auto-detected; `GPS_OBD_PROTOCOL=7` skips the scan), ~22 queries/s. The captured
  supported-PID reference is `reference/obd-supported-pids.md`. **OBD deferred:**
  udev-pin the EX to a stable device path (it's on bare `/dev/ttyUSB0`); an optional
  ignition-switched dongle feed for zero parked drain (the software gate covers it
  today); DTC storage + a check-engine surface; the on-demand rate-table demand overlay
  (an MQTT control topic with TTL-decayed demands, so a live gauge can raise a PID's
  rate without a second serial client); a dedicated ~1 Hz driving gauge (Home's
  `/api/status` glance covers the basics); the load-colored trail is part of frontend.md's
  "trail color-by" deferred item.
- **Victron reader** (`sensors/victron_reader.py`) — house power as a sensor: bridges
  the van's **Victron Venus OS GX** into the bus over **two MQTT clients**. A *source* on
  the GX's own broker (authenticated; subscribes `N/+/{system,solarcharger,vebus}/#`,
  learns the portal-id, and sends the **keepalive** Venus needs to keep publishing)
  caches latest values via an instance-wildcarded topic→column map; a *sink* emits one
  `sensors/house/victron` snapshot per 30 s to the Pi broker. **Not engine-gated** (the
  GX runs 24/7); a **staleness watchdog** flips the stream offline rather than republish
  a frozen cache when the GX goes silent. `--fake` mode for desk testing. The GX MQTT
  password is the only secret — via `/etc/default/gps-victron` on the Pi, never committed.
- **System reader** (`sensors/system_reader.py`) — the Pi as a sensor: publishes its
  own host metrics (CPU temp, 1-min load, memory %, root + NVMe disk %, NVMe free GB,
  uptime, and the `vcgencmd get_throttled` bitmask) to `sensors/pi/system` on a 30 s
  interval via the shared `run_simple_publisher`. No external source, driver, or
  secret — every metric is a stdlib read of `/proc`/`/sys`, `os.statvfs`,
  `os.getloadavg`, or `vcgencmd`, and each read is defensive (an absent source yields
  None for that one metric, so it degrades cleanly off-Pi). Not gated: the Pi is always
  on, so it runs continuously like Victron. `--fake` mode for desk testing. `throttled`
  is stored raw (0 = healthy), a cell like the Victron enum states rather than plotted.
- **OpenWrt reader** (`sensors/openwrt_reader.py`) — network infra as a sensor:
  SSH-polls a router (van-edge; multi-target via `--host/--node`, ifaces via
  `--wan-iface/--halow-iface`) in **one `sh -s` round-trip per 30 s poll** — a fixed
  BusyBox snippet with marker-wrapped sections, parsed back per section
  (`tools/openwrt_probe.py` imports the same helpers). Auth is the Pi's SSH key on the
  router's `/etc/dropbear/authorized_keys` (`BatchMode=yes`: no key → fast fail, never
  a password hang; the unit sets `HOME` explicitly since systemd doesn't for `User=`).
  Throughput columns are reader-side `/proc/net/dev` counter deltas (NULL + reseed
  across a router reboot). HaLow link metrics come from standard `iw`/`iwinfo` (the
  Morse driver serves nl80211); the radio's die temp comes from `morse_cli stats`,
  grepped on the router — the **mt7621 SoC has no temp sensor at all** (no thermal
  zone, no hwmon). `wan_up` (ubus logical-interface state) is distinct from
  `wan_ping_ms` (NULL = internet dark — signal, not failure, off-grid). Trap: `ubus
  call <path> <method>` takes path and method as separate args (`ubus call system
  info`, not `system.info`). `--fake` for desk testing.
- **Dahua fleet reader** (`sensors/dahua_reader.py`) — the recording fleet as sensors:
  one process CGI+RPC2-polls the NVR (node `van-nvr`, type `nvr`) and each active
  camera (`van-cam-*`, type `camera`) every 60 s, publishing five streams through
  `run_fleet_publisher` — **one MQTT session per stream**, because an LWT is
  per-connection (a shared client would leave dead streams stale-online). Two device
  protocols: digest-auth **CGI** for storage state / VideoLoss / clock / record mode,
  and the WebUI's **RPC2** JSON API (`sensors/dahua_rpc.py`: challenge login, session
  id **rotates on login**, object-style `factory.instance` handles) for what the CGI
  501s — CPU/memory everywhere, HDD SMART (temp 194, realloc 5, power-on-hours 9) on
  the NVR, uptime on cameras. Traps: the NVR redirects HTTP→HTTPS (self-signed —
  `verify=False`; RPC2 must go **straight to https** or the redirect drops POST
  bodies); `getCurrentTime` returns **TZ-local** time and the fleet's TZ config is
  inconsistent, so `clock_offset_s` folds to the nearest whole hour and reads true NTP
  drift; camera `getUpTime`'s **`Total` resets on boot, `Last` accumulates** (names
  misleading — verified live); no camera die-temp exists on these IPC models. Failure
  semantics: a down camera publishes an `online=0` row (outages chart), a down NVR is
  a dropped reading, and a device's first connection error early-outs its cycle. The
  shared WebUI password comes from `/etc/default/gps-dahua` (root-owned 600, out of
  git; the reader exits 2 without it). `--fake` for desk testing;
  `tools/dahua_probe.py` (imports the fleet table) re-surveys the CGI endpoint set.
- **Ingest subscriber** (`mqttbus/ingest.py`) — the **only** writer of sensor data.
  Subscribes `sensors/#`, auto-registers sensors, canonicalizes timestamps, inserts
  per-type rows, applies LWT status. The per-type INSERT is **column-driven** off a
  shared `READING_TABLES` spec (`api/sensor_schema.py`, imported by both ingest and the
  `/sensors` read route) — a new stream is a spec entry, not an ingest code branch.
  Persistent session + QoS-1 so a restart loses nothing. Heartbeat with a
  dropped-reading reason breakdown, logger-style.
- **Alarm subscriber** — planned, not built yet (`mqttbus/alarms.py`).
- **Web app** (`api/routes/sensors.py`) — `/api/sensors`, `/api/sensors/<id>/readings`,
  and the bucketed `/api/sensors/series` JSON. Read-only, DB-backed (no MQTT): the SPA's
  Systems (current values) and Trends (charts) views consume these. This is the non-live
  half of the live-readout goal; it sidesteps the broker websockets blocker entirely.
  **Series contract** (the Trends engine): metrics are addressed `<sensor_id>.<column>`
  (any numeric column; the picker offers only `chart:true` ones), the server buckets on
  epoch seconds (`bucket_ms = max(ceil(window_ms / buckets), 1s)`, default ~1000 buckets,
  cap 2000) with avg + min/max per bucket, one query per distinct sensor covering all its
  requested columns, and scatters results onto a **dense** `start…end` grid — nulls for
  empty buckets, so the client renders gaps honestly. Presentation fields ride along from
  `METRIC_META`; the client builds the chart entirely from the response.
- **Browser (live)** — the planned push path via MQTT-over-WS, **blocked:** the Pi's
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
`victron_readings`, `system_readings`, `openwrt_readings`, `nvr_readings`,
`camera_readings`, and the `alarm_rules` / `alarm_events` tables
(created now, exercised when alarms land). All
timestamps go through `api.db.canonical_timestamp` so they join cleanly against
`gps_points`. FKs are logical/unenforced, matching the trips↔points style. Each
per-type table's columns are declared once in `api/sensor_schema.py` `READING_TABLES`,
which drives both the ingest INSERT and the read route. Its presentation companion
`METRIC_META` (same module, keyed by column) is the single source of truth for how a
metric is *shown* — label, unit, decimals, chart-or-cell, color, alt-unit conversion,
fixed y-axis range, and group — served by `/api/sensors` so the viewer renders from
data instead of a parallel hardcoded map (a test pins every column to a meta entry, so
a new stream can't ship unlabelled the way Victron once did). `READING_TABLES` stays
storage-only; `mqttbus` ingest imports it alone, keeping the two concerns apart.

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
from absolute load + RPM + λ at read time (`common/obd.py`; the single constant `K` is
the calibration lever — a tank-to-tank fill-up calibration is pending, and because
derivation happens at read time it re-scales all history at once when it lands). The
column set is the `READING_TABLES` spec, not hand-written SQL.

`victron_readings` is the wide table for the house-power stream — battery (SoC, voltage,
current, power, temp, consumed Ah, time-to-go, state), solar (PV power/voltage, today's
yield, charger state), DC system load, AC in (power/current/source) + AC consumption, and
inverter state/mode. Sourced almost entirely from the GX's stable `system/0` aggregate;
device-level services (`solarcharger`, `vebus`) are matched with the instance segment
wildcarded, since Venus instance numbers aren't stable across reconfigurations. The
column set is the `READING_TABLES` spec.

`openwrt_readings` is the router stream — load/mem/uptime, WAN state + throughput
deltas + ping RTT, HaLow RSSI/noise/link rates/station count/radio temp, DHCP leases,
conntrack. `load_1m`/`mem_used_pct`/`uptime_s` deliberately reuse the Pi `system`
stream's column names so they share `METRIC_META` rows (it's keyed by column name).

`nvr_readings`/`camera_readings` are the recording-fleet streams — NVR: HDD health
(`hdd_ok`/`hdd_err_partitions` from CGI; SMART `hdd_temp_c`/`hdd_realloc_sectors`/
`hdd_power_on_h` via RPC2 — used-bytes carries no signal under loop recording),
`channels_video_loss` (the NVR-side camera-down signal; `getCameraState` is 501 on
this firmware), CPU/mem, clock drift. Cameras: `online` (0/1 — poll success),
CPU/mem/uptime, clock drift, `record_mode` enum. Column sets are the
`READING_TABLES` spec.

## Conventions

Topics (`mqttbus/topics.py`):

```
sensors/<node>/<type>           # readings (JSON)
sensors/<node>/<type>/status    # retained health flag (LWT: "offline"); free-form per
                                # stream — all use online/offline, OBD adds no_adapter/no_car
alarms/<rule_id>                # retained alarm state (alarms not built yet)
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

Services: `deploy/{mqtt-ingest,sensor-bme680,sensor-obd,sensor-victron,sensor-pi,sensor-openwrt,sensor-dahua}.service`
(slot into the existing systemd + post-receive model). The hook copies the new unit
files and `mosquitto.conf` when `deploy/` changes, restarts `mqtt-ingest` when enabled,
and restarts the `sensor-*` readers only when `sensors/` changes **and** the unit is
enabled — so a host without that hardware never starts a crash-looping reader. (The
post-receive hook enumerates the reader units to restart; a newly added unit like
`sensor-pi` needs adding to that list on the Pi, plus a one-time `systemctl enable --now
sensor-pi`.)

One-time Pi setup is installing mosquitto and enabling the units.
`mosquitto` + `mqtt-ingest` run on the Pi and ingest whatever publishes. The
`sensor-bme680` service stays **disabled** — the BME680 moved to the ESPHome node, so
the Pi-attached reader has no hardware (and the CM5 GPIO I2C bus is still off).
`sensor-obd` **is enabled** (node `van`, `/dev/ttyUSB0`): it owns the OBDLink EX and
self-gates on engine state, so it idles cleanly when the van is parked. `sensor-victron`
**is enabled** (node `house`): it bridges the always-on Venus GX and runs continuously
(not engine-gated), reading the GX MQTT password from `/etc/default/gps-victron`
(root-owned, `chmod 600` — out of git; create it before enabling, or the GX rejects the
unauthenticated connection). `sensor-pi` **is enabled** (node `pi`): it needs no
hardware, secret, or gating (every metric is a local stdlib read), so unlike the other
readers it just runs continuously. `sensor-openwrt` **is enabled** (node `van-edge`):
its one-time setup was authorizing the Pi's ed25519 key on van-edge root
(`/etc/dropbear/authorized_keys`, done 2026-07-02). `sensor-dahua` **is enabled**
(nodes `van-nvr` + `van-cam-*`): its secret is `/etc/default/gps-dahua`
(`GPS_DAHUA_PASSWORD=…`, root-owned 600 — creating it is a user-run step in a real
terminal; the `!` bash-prefix has no TTY for a silent password prompt).

The ESP32 node is flashed from a dev host, not the Pi:
`uv tool run esphome run firmware/cabin-bme680.yaml` (first flash over USB, OTA after;
copy `firmware/secrets.yaml.example` → `secrets.yaml` first). See `firmware/README.md`.

**Offline:** mosquitto is a local broker; MQTT.js will be vendored when the live push
path lands; the ESP32
NTP-syncs off the Pi and talks only to the local broker. Nothing here reaches the
internet at runtime. Installing mosquitto/`paho-mqtt` and **building the ESPHome
firmware** (ESP-IDF toolchain + BSEC2) are online prep steps, same as tile pre-caching.
