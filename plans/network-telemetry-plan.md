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

### Phase 0 — Probe & lock the schema  *(read-only; run on the Pi)*
The exact metric→source mapping has real unknowns (HaLow RSSI command under the Morse
driver; whether mt7621 exposes a thermal zone; exact Dahua CGI endpoint shapes). Resolve
them with two Phase-0 probe tools before freezing any columns — the project's established
habit (`tools/obd_probe.py`, `tools/civ_probe.py`).

- `tools/openwrt_probe.py` — SSH to a target, dump candidate sources (`/proc/loadavg`,
  `/proc/meminfo`, `/proc/uptime`, `/proc/net/dev`, thermal zones, `iw`/morse station
  dump, `/tmp/dhcp.leases`, conntrack count, WAN ping) and print what's available.
- `tools/dahua_probe.py` — digest-auth GET the candidate CGI endpoints (storage/HDD,
  recording state, channel/camera state, system info) and print the raw responses.

**Output:** confirmed column sets for each table → fills in Phases 1–2.

### Phase 1 — OpenWrt router stream
- `api/db.py`: `openwrt_readings` table (columns from Phase 0).
- `api/sensor_schema.py`: `READING_TABLES['openwrt']` + `METRIC_META` rows.
- `sensors/openwrt_reader.py`: SSH single-target reader, delta-state throughput,
  `--host/--node/--fake/--once`. Reuses `sensors/runner.py`.
- `deploy/sensor-openwrt.service` (enabled-gated — needs the SSH key). Add to the
  post-receive restart list.
- One-time Pi setup: generate + authorize the SSH key on van-edge; enable the unit.
- Validate against van-edge.

### Phase 2 — Dahua fleet stream
- `api/db.py`: `nvr_readings` + `camera_readings` tables (columns from Phase 0).
- `api/sensor_schema.py`: `READING_TABLES['nvr']`/`['camera']` + `METRIC_META` rows.
- `sensors/dahua_reader.py`: HTTP-CGI fleet reader (NVR + N cams → multi-node publish),
  digest auth, secret from `/etc/default/gps-dahua`, `--fake/--once`.
- Multi-stream publisher support in `sensors/runner.py` (see Open questions).
- `deploy/sensor-dahua.service` (enabled-gated — needs the secret). Add to restart list.
- One-time Pi setup: create `/etc/default/gps-dahua`; enable the unit.
- Validate against the fleet.

### Phase 3 — Presentation & tests
- `METRIC_META` grouping/colors/enum codecs so Systems + Trends render sensibly.
- Tests: reader parse logic (probe-output fixtures), `METRIC_META`-covers-every-column
  guard (already enforced by the existing test), ingest of the new types.
- Fold the durable bits into `.claude/modules/sensors.md`; drop this plan.

## Open questions
- **Multi-stream publisher shape.** `run_simple_publisher` is one-topic. For the Dahua
  fleet, either extend `runner.py` with a `run_multi_publisher` (one process, N sessions)
  or run one unit per device. Leaning multi-publisher (one connection story, fewer
  units) — decide after Phase 0 confirms the per-device column sets.
- **HaLow link metrics.** Whether per-station RSSI/MCS is reachable under the Morse
  driver, and in what form — Phase 0 answers this. If unavailable, fall back to
  associated-station count only.
- **NVR vs per-camera camera health.** Camera online/offline is derivable from the NVR
  in one shot; per-camera uptime needs hitting each cam. Phase 0 shows what each source
  gives, then decide whether cameras are their own nodes or NVR-derived channels.
