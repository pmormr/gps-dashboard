# Network-Device Telemetry Plan

Extend the sensor platform to pull **health/status telemetry** from existing van-LAN
network infrastructure, using the established readings pipeline (Pi-side reader →
MQTT → `mqttbus/ingest.py` → SQLite → `/sensors`). No new transport, no new UI model.

Two streams are in scope (decided 2026-07-01):

1. **van-edge router** — MorseMicro HaLowLink 2, OpenWrt 23.05.6, MIPS mt7621.
   Collected by **SSH-poll from the Pi** (root@192.168.42.1).
2. **Dahua fleet** — the N41C2P2 NVR + its 4 Dahua cameras (`.51`–`.54`), all sharing
   one HTTP CGI API and one WebUI password. Collected by **HTTP-CGI poll from the Pi**.

**Explicitly out of scope** (may revisit):
- Camera **motion/AI events** — a discrete-event timeline (Dahua `eventManager`
  long-poll), which needs a *new* event table + ingest path + UI, not the readings
  model. Deferred as a separate future capability.
- **Hikvision cams** (`.55`/`.56`) — not recording, undocumented firmware, different
  API (ISAPI). Deferred.

All target devices are on the van LAN (192.168.42.0/24) with the Pi at `.178`, so
polling is a direct LAN reach — fully offline-compatible, no tunneling.

## Design decisions

- **Reader-on-Pi, not on-device.** Router metrics come from the Pi SSHing in and
  running a fixed remote snippet (one round-trip per interval, parse `key=value`).
  Keeps all code in the existing deploy pipeline; nothing to maintain on the MIPS box.
  Auth = an **SSH key** (Pi → van-edge root), not a password — simpler than the
  Victron secret model.
- **One reader per fleet, multi-node output.** The Dahua reader polls the NVR *and*
  each camera in one process, publishing one reading per device (nodes `van-nvr`,
  `van-cam-front`, …), matching the vault hostnames. Requires a multi-stream variant
  of the publisher loop (see Open questions).
- **Router reader is multi-target by design.** `--host/--node` args so the same code
  can later monitor the HaLow bridge (`rex-ahap1`) and `rex-edge`. Only van-edge is
  wired now.
- **Throughput needs reader-side delta state.** rx/tx are cumulative counters; the
  reader holds last-poll values and emits a rate (kbps). A departure from the
  stateless simple readers — the sensor object carries state (fine; the object model
  allows it).
- **Dahua secret** = the shared WebUI password via `/etc/default/gps-dahua`
  (root-owned, 600, out of git), same pattern as `/etc/default/gps-victron`. Digest
  auth via `requests` (already a dep).
- **Enum/state columns** (recording state, disk health, WAN up/down) render via the
  existing `codec='enum'`/`'bitmask'` + `codes` display path, like the Victron/throttle
  states — cells, not charts.

## Phases

### Phase 0 — Probe & lock the schema  *(DONE 2026-07-02)*
Both probe tools landed and ran against the live fleet: `tools/openwrt_probe.py`
(one-SSH-round-trip source survey, marker-parsed sections) and `tools/dahua_probe.py`
(digest-auth CGI survey, NVR + 4 cams). Re-run either any time to re-verify a source.

**Router findings (van-edge):**
- The Morse driver serves **standard nl80211**: `iw dev wlan0 station dump` gives
  per-station signal/bitrate/MCS/retries; `iwinfo wlan0 info` gives device-level
  Signal/Noise; `iwinfo wlan0 assoclist` gives per-station SNR. No vendor CLI needed
  (`/sbin/morse_cli` exists but is not required).
- **No temperature source** — mt7621 exposes no thermal zone and no hwmon. Column out.
- `ubus call system info` (note: `ubus call <path> <method>`, space-separated) returns
  pre-parsed JSON: load, memory, root/tmp fs. `ubus call network.interface.wan status`
  gives WAN up/uptime/device. Interfaces of interest in `/proc/net/dev`: `wan`,
  `wlan0` (HaLow), `br-lan`.
- Leases, conntrack count/max, and WAN ping (avg RTT ~43 ms via Starlink) all parse.

**Dahua findings (fleet, FW 4.003/2.800):**
- The **NVR redirects HTTP→HTTPS with a self-signed cert** — poll with digest auth +
  `verify=False`. Cameras answer plain HTTP.
- `getUpTime`/`getMemoryInfo`/`getCPUUsage` are **not implemented on any device**
  (501/400). No uptime/host-resource columns anywhere.
- NVR: `storageDevice.cgi getDeviceAllInfo` works (per-partition `IsError`, device
  `State=Success`; used==total always under loop recording — used bytes carry no
  signal). `LogicDeviceManager getCameraState` is **501**; the live camera-down signal
  is `eventManager getEventIndexes&code=VideoLoss` ("No Events" = all channels up,
  else the down channel indexes). `getCameraAll` + `ChannelTitle` give the
  channel→camera identity map.
- Cameras: identity (`getSystemInfo`), clock (`getCurrentTime`), and `RecordMode`
  config serve; nothing else does. Per-camera columns are lean by construction.

### Phase 1 — OpenWrt router stream  *(DONE 2026-07-02 — live on the Pi)*
Built, deployed, validated against van-edge, `sensor-openwrt` enabled: rows land in
`openwrt_readings` every 30 s and `/api/sensors` serves the `van-edge`/`openwrt`
stream. The reader (`sensors/openwrt_reader.py`) owns the marker build/parse helpers;
`tools/openwrt_probe.py` imports them. `mem_used_pct` replaced the planned
`mem_available_pct` so the column shares the Pi system stream's META row (same for
`load_1m`/`uptime_s`). The post-receive hook gained the `sensor-openwrt` restart
block (hook lives on the Pi, not in git).

Locked columns for `openwrt_readings` (all NULLable; one row per poll per node):
`load_1m` · `mem_available_pct` · `uptime_s` (reboot sawtooth) · `wan_up` (0/1 enum,
ubus) · `wan_rx_kbps`/`wan_tx_kbps` (delta on `wan`) · `halow_rx_kbps`/`halow_tx_kbps`
(delta on `wlan0`) · `halow_stations` · `halow_rssi_dbm`/`halow_noise_dbm` (iwinfo
info) · `halow_tx_mbps`/`halow_rx_mbps` (assoclist bitrates — the bridge link) ·
`halow_temp_c` (Morse radio die temp via `morse_cli stats`, added post-launch — the
mt7621 SoC has no sensor but the MM8108 does) ·
`dhcp_leases` · `conntrack_count` · `wan_ping_ms` (NULL when unreachable; the
internet-actually-works signal, distinct from `wan_up`).

- `api/db.py`: `openwrt_readings` table.
- `api/sensor_schema.py`: `READING_TABLES['openwrt']` + `METRIC_META` rows.
- `sensors/openwrt_reader.py`: SSH single-target reader (one `sh -s` round-trip per
  poll, marker parsing like the probe), delta-state throughput,
  `--host/--node/--fake/--once`. Reuses `sensors/runner.py`.
- `deploy/sensor-openwrt.service` (enabled-gated). Add to the post-receive restart list.
- ~~SSH key setup~~ **done 2026-07-02**: the Pi's ed25519 key is authorized on
  van-edge (`/etc/dropbear/authorized_keys`) and verified.
- Validate against van-edge; enable the unit.

### Phase 2 — Dahua fleet stream  *(DONE 2026-07-02 — live on the Pi)*
Built, deployed, validated against the fleet, `sensor-dahua` enabled with the secret
in `/etc/default/gps-dahua`: all five streams (`van-nvr` + 4 cams) publish through
the new `run_fleet_publisher` (one session **per stream** — an MQTT LWT is
per-connection) and land in `nvr_readings`/`camera_readings` every 60 s. One
post-validation fix: `getCurrentTime` returns **TZ-local** time (NVR −4 h, cams
−5 h — the fleet's TZ config is inconsistent; vault's NVR-TZ=UTC note is wrong), so
`clock_offset_s` folds to the nearest whole hour and reads true NTP drift (~+3 s
fleet-wide vs the Pi's stratum-1 clock).

Locked columns. `nvr_readings` (node `van-nvr`): `hdd_ok` (0/1 from `State=Success`) ·
`hdd_err_partitions` (count of `IsError=true`) · `channels_video_loss` (count from the
VideoLoss index list; 0 = all recording) · `clock_offset_s` (getCurrentTime − Pi
clock). `camera_readings` (nodes `van-cam-front/-blind-left/-blind-right/-rear`):
`online` (0/1 — poll answered; a down cam publishes `online=0`, other columns NULL) ·
`clock_offset_s` · `record_mode` (enum 0=auto/1=manual/2=off).

- `api/db.py`: `nvr_readings` + `camera_readings` tables.
- `api/sensor_schema.py`: `READING_TABLES['nvr']`/`['camera']` + `METRIC_META` rows.
- `sensors/dahua_reader.py`: HTTP-CGI fleet reader (NVR + 4 cams → multi-node publish),
  digest auth + `verify=False`, secret from `/etc/default/gps-dahua`, `--fake/--once`.
- Multi-stream publisher support in `sensors/runner.py` (decided: one process, N
  streams — see Open questions).
- `deploy/sensor-dahua.service` (enabled-gated — needs the secret). Add to restart list.
- One-time Pi setup: create `/etc/default/gps-dahua` (root:root 600,
  `GPS_DAHUA_PASSWORD=…`); enable the unit.
- Validate against the fleet.

### Phase 3 — Presentation & tests
- `METRIC_META` grouping/colors/enum codecs so Systems + Trends render sensibly.
- Tests: reader parse logic (probe-output fixtures), `METRIC_META`-covers-every-column
  guard (already enforced by the existing test), ingest of the new types.
- Fold the durable bits into `.claude/modules/sensors.md`; drop this plan.

## Open questions — all resolved by Phase 0 (2026-07-02)
- **Multi-stream publisher shape** → extend `runner.py` with a multi-publisher (one
  `dahua_reader` process, 5 nodes/topics). The per-device column sets are so lean that
  one-unit-per-device would be all overhead.
- **HaLow link metrics** → fully available via standard `iw`/`iwinfo`; the
  station-count-only fallback is moot.
- **NVR vs per-camera camera health** → both, cheaply: cameras are their own nodes
  (direct poll gives clock/record-mode, and poll success *is* the online signal); the
  NVR contributes `channels_video_loss` as the recording-side cross-check (a cam can
  be pingable yet not delivering video).
