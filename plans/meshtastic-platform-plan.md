# Meshtastic Platform Plan (LILYGO T-Beam S3 Core)

> Living plan. Check items off as they land, record decisions inline. Markup
> welcome — leave comments against any row and we'll resolve them before writing
> code.
>
> **Iteration 1** (2026-06-29) — scoped the subsystem and **closed Phase 0
> feasibility on the live node** (probed at `192.168.42.179`). Locked the
> integration vector (**M1 — native TCP-API daemon**, not MeshMonitor), the scope
> (positions on Map + Systems health + two-way messaging — telemetry-into-sensors
> deferred), and the daemon-owns-the-connection model (M2). Open before Phase 1
> coding: the send-IPC mechanism (M-IPC), a stable node address, and the new nav
> tab name/icon.
>
> **Iteration 2** (2026-06-29) — clarified the **north-star: a hiking command
> center**. A person carries a paired node out of cell/WiFi but in LoRa range of
> the van; they message the van **two-way**, and the van **proactively polls** them
> for position + connectivity. This elevates messaging to core (**M8** — DM +
> delivery ACK as the safety link) and adds **active polling** of tracked nodes
> (**M7**). Phases reshaped: 2 = the active link (DM/ACK + poll), 3 = the hiking
> command-center UI; telemetry/track-lines/correlation slide to Phase 4.

## Context

A Meshtastic node — a **LILYGO T-Beam S3 Core** (onboard u-blox GPS + SX1262 LoRa
+ ESP32-S3) — was added to the van and joined the van WiFi (`vannet`) at
`192.168.42.179`. It's a peer telemetry/comms stream alongside GPS, sensors, OBD,
Victron, drone, and radio, and reuses the project's shape: a standalone Pi-side
service + Flask routes + a page, with data on the **same** SQLite time axis so mesh
positions correlate with the van's own GPS track.

**North-star use case — a hiking command center.** Someone carries a paired
Meshtastic node on a hike, beyond cell/WiFi but within LoRa range of the van. They
**message the van two-way** (the safety/comms link), and the van **proactively
polls** their node for **position and connectivity** on a schedule — so the van
always holds a recent fix and a reachable / last-contact signal even when the
hiker's node isn't broadcasting. This makes **messaging + active polling core**
(not deferred extras) and points the map overlay at *live hiker tracking*.

**Meshtastic exposes three interfaces on the node:**

- **TCP API — `:4403` (protobuf).** Full fidelity: streams every received packet
  (positions, nodeinfo, telemetry, text), serves the persisted node DB on connect,
  and can **send** text. This is the vector we build on (M1). The connection is
  stateful and single-owner in practice — only a few concurrent clients, each
  pays a full config handshake on connect.
- **HTTP — `/json/report` (JSON).** The van node's *own* device telemetry only
  (battery, airtime, channel utilization, WiFi RSSI). No node DB, no positions, no
  messages. A zero-decode liveness/fallback signal.
- **MQTT.** Disabled, and pointed at the *public* server. We are **not** using it
  (M1). If it were ever enabled it must target the van's local mosquitto with
  `mapReportingEnabled` off — otherwise the van's position publishes to the global
  map. (Out of scope; noted so it isn't enabled by accident.)

What the integration delivers (the chosen scope):

- **Mesh node positions on the existing MapLibre map** — other nodes as a Layers-
  panel overlay next to the van track / drone tracks / annotations. The standout
  off-grid situational-awareness feature. Renders offline (our basemaps already do).
- **Van-node health in Systems** — battery / channel utilization / air-util / RSSI
  / nodes-heard / service state, as a Systems card.
- **Two-way text messaging** — send/receive mesh text from a new **Mesh** tab.

## Decisions

- **M1 — Native build on the TCP-API daemon (not MeshMonitor).** Build mesh data
  into the dashboard rather than deploy a standalone console. Rationale: the value
  is mesh nodes on *our* offline map + Systems + one correlated SQLite/time axis,
  in one Python/uv/systemd stack. MeshMonitor (Yeraze/meshmonitor, BSD-3,
  React+Node+**Docker**, arm64 image exists) is a polished mesh console but a
  separate app/port/DB/stack with online map tiles — it can't put nodes on the van
  map or correlate with the GPS track, and it'd stand up Docker on a deliberately-
  Python Pi. Kept as a **reference implementation / fallback**, not a dependency.
- **M2 — Daemon owns the single TCP-API connection.** A long-lived standalone
  service holds the `:4403` connection (peer to the logger/processor — a standalone
  SQLite writer, *not* on the MQTT bus), receives packets → writes the mesh tables,
  and is the **sole** API client (the connection limit + per-connect handshake make
  a transient per-request Flask connection wrong). Mirrors the
  "daemon-owns-the-device, app is a thin client" model proven by `radio-control`
  (rigctld). Uses the official **`meshtastic` Python lib** over TCP (M3).
- **M3 — `meshtastic` Python lib as the protobuf layer.** Adds `meshtastic`
  (transitively `protobuf`, `pyserial`, `pypubsub`, and `bleak` — BLE, unused but
  not separable). Runtime is pure-Python protobuf over a TCP socket → **offline-
  clean** (internet only at `uv sync`, cached). Chosen over hand-rolling a protobuf
  client. **Needs sign-off (the project asks before adding deps).**
- **M4 — Data model (mirrors the drone tier).** Mesh is its own entity tier,
  fully rebuildable from the live node DB on reconnect; never touches `gps_points`/
  `track_points`. Schema added in `api/db.py` (the project's migration home),
  canonical ms-UTC timestamps throughout:
  - `mesh_nodes(node_num PK, node_id, long_name, short_name, hw_model, role,
    public_key, hops_away, snr, battery_level, voltage, channel_util, air_util_tx,
    uptime_s, last_lat, last_lon, last_alt, first_heard_utc, last_heard_utc, is_self,
    tracked, poll_interval_s, last_contact_utc)` — registry, one row per node, latest
    state embedded (like `sensors` rows / `drone_flights`). Updated on NODEINFO +
    telemetry + position packets. `tracked` flags a node the poller actively probes
    (M7); `last_contact_utc` = last confirmed round-trip (passive or polled).
  - `mesh_positions(id, node_num, timestamp, lat, lon, altitude, source, snr)` —
    position time series (mirror `drone_track_points`). `source` in `broadcast`|
    `requested` (M7 polls land here too). Backs the Map overlay (latest per node
    now; per-node track lines later).
  - `mesh_checks(id, node_num, timestamp, kind, ok, rtt_ms, hops, snr)` — active-poll
    results (M7). `kind` in `position`|`traceroute`; one row per poll attempt
    (`ok=0` on timeout). Backs the connectivity/last-contact signal + the
    silent-node alarm (M9).
  - `mesh_messages(id, timestamp, from_num, to_num, channel, text, snr, rssi,
    hops_away, direction, status)` — text both ways. `direction` in `rx`|`tx`;
    `status` in `queued`|`sent`|`acked`|`failed` (the DM delivery ACK — M8). Backs
    the chat thread **and** is the send queue: Flask inserts a `tx`/`queued` row, the
    daemon drains and updates status (folds the outbox into one table — see M-IPC).
- **M5 — Enabled-gated, address-configured service.** `deploy/mesh-listener.service`,
  enabled-gated like `radio-control`/`sensor-obd`/`sensor-victron` (won't crash-loop
  if the node is unreachable). Node host via env (`GPS_MESH_HOST`, default
  `192.168.42.179`); the unit installs via the existing `deploy/*.service` glob, plus
  an enabled-gated restart stanza in the post-receive hook (mirror radio 1i). The
  node's DHCP lease should be **reserved** so the address is stable (M-ADDR).
- **M6 — Privacy / channel.** Receiving positions/telemetry/text and sending text over
  the TCP API need **no** node reconfig (unlike the MQTT path), and we don't enable
  MQTT or map reporting. The node is on the public default-key LongFast channel at
  reduced position precision (`positionPrecision: 13`, ~km). For the hiking use case
  that's both coarse and public — so a **private, full-precision channel** shared by
  the van + hiker nodes is likely wanted (better fixes, off the public mesh, PKC DMs).
  Open (M-CHAN); not a Phase-1 blocker (passive ingest works on any channel).
- **M7 — Active polling of tracked nodes.** The daemon runs a poller: for each
  `tracked` node, on `poll_interval_s`, send a **position request** (Position packet,
  `want_response`) and record a `mesh_checks` row — the reply yields position +
  reachability + SNR/hops in one round-trip. **Adaptive + conservative:** skip the
  active poll if a fresh `broadcast` position already arrived inside the window; only
  `tracked` nodes are polled; default interval in **minutes** (LoRa airtime + the
  remote battery + mesh congestion are the cost — US 915 has no hard duty cycle but
  the budget is real). Traceroute is an optional heavier diagnostic (`kind`).
- **M8 — DM + delivery ACK as the comms link.** Van↔hiker messaging is **direct
  messages** to a node num (PKC-encrypted; the node has PKC on), **not** channel
  broadcast — DMs get a delivery ACK, so `mesh_messages.status` (`sent`→`acked`/
  `failed`) is a real "did it get through?" signal, first-class in the UI. Channel
  chat can come later; the safety link is DMs.
- **M9 — Silent-node alarm (reuse the alarm platform).** "Tracked node silent
  > N min" is the core safety signal — wire it as an `alarm_rules` row raising
  `alarm_events` (reuse the sensor-platform alarm tables) off `last_contact_utc`,
  rather than building new alerting. Design-in; lands with the hiking UI (Phase 3).

## Phase 0 — Feasibility — **DONE (2026-06-29)**

Probed the live node (`uvx meshtastic --host 192.168.42.179 --info` + `/json/report`):

- [x] **Reachable** from the dev laptop over the site link (~60–100 ms) and from the
      Pi's LAN; TCP `:4403` open, HTTP `/json/report` + `/api/v1/...` served.
- [x] **Identity:** `!435a7e7c` / num `1130004092`, "Meshtastic 7e7c", hw
      `LILYGO_TBEAM_S3_CORE`, firmware **2.7.15** (VANILLA), role **CLIENT**, PKC on.
- [x] **Radio:** US region, **LONG_FAST**, 906.875 MHz, hop limit 3, txPower 30; single
      PRIMARY channel on the **default public key** (`AQ==`).
- [x] **GPS:** `gpsMode: ENABLED` (no fix yet — rebooted), broadcast 900 s / smart-on.
- [x] **MQTT** disabled (public server); **telemetry module** all off.
- [x] **Mesh DB:** 200 nodes known, **121 with positions** (mostly stale pre-reboot) —
      confirms the node DB is rich and worth surfacing.
- [x] **Send path exists:** the TCP API `sendText` is available (firmware supports it).
- **Security note (not for the repo):** the config dump exposes the node's private key
  and the WiFi PSK — these go in **neither** the repo, the vault, nor memory.

## Phase 1 — Ingest daemon + node DB + Map overlay + Systems health (read path)

The bulk. Order roughly top-to-bottom.

- [ ] **1a — dep:** add `meshtastic` to `pyproject.toml` + `uv.lock` (M3); confirm the
      offline cache carries it + transitive deps. **(gated on M3 sign-off.)**
- [ ] **1b — schema:** `mesh_nodes` / `mesh_positions` / `mesh_messages` in `api/db.py`
      (M4), with indexes on `(node_num, timestamp)` + `last_heard_utc`.
- [ ] **1c — daemon:** `meshd/listener.py` (name TBD) — `TCPInterface(GPS_MESH_HOST)`,
      subscribe to receive events (position / nodeinfo / telemetry / text), upsert
      `mesh_nodes`, append `mesh_positions`, insert `rx` `mesh_messages`. Seed the
      registry from the node DB served on connect. Reconnect with backoff (mirror the
      logger's resilience); mark `is_self` for the van node.
- [ ] **1d — service:** `deploy/mesh-listener.service` enabled-gated (M5) + the
      post-receive enabled-gated restart stanza.
- [ ] **1e — routes:** `api/routes/mesh.py` — `GET /api/mesh/nodes` (registry + latest
      state, `bbox=`/`start=`/`end=` filters like `/api/drone/flights`),
      `GET /api/mesh/status` (van-node health from the `is_self` row). Read-only.
- [ ] **1f — Map overlay:** a `mesh.ts` overlay controller + a Layers-panel "Mesh"
      toggle (mirror the drone overlay path), node markers (short name, colored by
      last-heard freshness / SNR), tap → popup (name, hw, battery, SNR, hops, last
      heard).
- [ ] **1g — Systems card:** van-node mesh health (battery / channel-util / air-util /
      RSSI / nodes-heard / service state) in `views/Systems.svelte`.
- [ ] **1h — tests:** packet→row mapping (pure, against captured `--info`/packet
      fixtures), `/api/mesh/*` route JSON against a temp DB (the `conftest.py` pattern).
- [ ] **1i — docs:** CLAUDE.md (new section, `/api/mesh/*` endpoints, structure tree,
      Offline-Constraint note for the `meshtastic` dep), and the device in the
      network-docs vault (`van-meshtastic.md`, **no keys**) + a memory pointer.

## Phase 2 — Active link (DM messaging + position/connectivity polling)

The comms link. DM send and the poller share one piece of plumbing — the daemon
sends a packet and tracks the response/ACK — so they land together.

- [ ] **2a — DM send IPC (M-IPC):** Flask `POST /api/mesh/messages` inserts a
      `tx`/`queued` `mesh_messages` row (dest = node num); the daemon drains queued rows
      (short poll), calls `sendText(..., wantAck=True)`, and updates `status`
      (`sent`→`acked`/`failed`) from the ACK callback. Recommended over a control socket:
      offline-robust, survives daemon restarts, trivially testable, LoRa airtime hides
      the poll interval.
- [ ] **2b — poller (M7):** per `tracked` node, on `poll_interval_s`, send a position
      request (`want_response`); record a `mesh_checks` row + land any returned fix in
      `mesh_positions` (`source='requested'`) and bump `last_contact_utc`. Adaptive
      (skip if a fresh `broadcast` arrived) + conservative defaults.
- [ ] **2c — read API:** `GET /api/mesh/messages?since=&with=` (DM thread + new),
      `POST /api/mesh/nodes/:num/track` (flag tracked + interval),
      `GET /api/mesh/nodes/:num` (latest position + connectivity from `mesh_checks`).
- [ ] **2d — tests + docs:** outbox drain + ACK status transitions; poller adaptivity +
      timeout→`ok=0`; route tests; CLAUDE.md.

## Phase 3 — Hiking command-center UI

The payoff view — live situational awareness for someone on the trail.

- [ ] **3a — tracked-hikers panel:** new nav item + route + `views/Mesh.svelte` — per
      tracked node: live position on the map, **last-contact age**, **signal bars**
      (SNR/hops from `mesh_checks`), battery, and the **DM thread** (list + composer,
      delivery-ACK ticks). Nav grows to 8 (name/icon — M-TAB; Attractions + Docs
      landed after this plan's last iteration, and drive-view contends for a slot too).
- [ ] **3b — silent-node alarm (M9):** an `alarm_rules` row ("tracked node silent
      > N min") raising `alarm_events` off `last_contact_utc`; surface on the panel +
      Home glance.
- [ ] **3c — map breadcrumb:** per-tracked-node track polyline from `mesh_positions`
      (the hiker's trail), distinct from the passive all-nodes overlay (1f).

## Phase 4 — Deferred / optional

- [ ] **Environmental telemetry → sensor platform.** Fold node env/power telemetry into
      `sensors`/readings + `/trends` charts (not in the chosen scope; revisit if a node
      carries sensors).
- [ ] **Channel chat** (broadcast threads) alongside the DM safety link (M8).
- [ ] **Correlation views** — mesh-node position vs. the van's GPS track over a time
      window (range/bearing to a node along the trip).

## Open questions (resolve in walk-through)

- **M-CHAN — private full-precision channel?** A shared van+hiker channel (PKC DMs,
  full position precision, off the public mesh) vs. staying on public LongFast. Affects
  fix quality + privacy, not the Phase-1 read path. Confirm direction (and whether to
  set it up on the nodes).
- **M-POLL — default poll cadence + adaptivity (M7).** Proposed: minutes-scale default
  (e.g. 3–5 min), per-node configurable, skip-if-fresh-broadcast. Confirm the number and
  whether to also support traceroute polls.
- **M-ALARM — silent-node threshold (M9).** "Tracked node silent > N min" — pick N
  (e.g. 15–30 min) at Phase 3.
- **M-IPC — send mechanism. PROPOSED (2a):** `mesh_messages` outbox row drained by the
  daemon (vs. a rigctld-style control socket). Confirm.
- **M-ADDR — stable node address.** Reserve `192.168.42.179` as a DHCP lease on the van
  router (preferred) vs. rely on the env override / mDNS. Confirm.
- **M-TAB — nav tab name + icon** for the hiking/messaging surface (e.g. "Mesh" 📡 /
  🕸️ / 💬). Minor; decide at 3a.
- **M3 sign-off — add the `meshtastic` dep** (pulls `protobuf`/`pyserial`/`pypubsub`/
  `bleak`). Runtime-offline-clean. Approve?
- **Daemon package name** — `meshd/` (peer to `logger/`/`processor/`) vs. another home.
  Minor.

## Parked — sibling subsystem: phone-over-IP position tracking

> Out of scope here; raised 2026-06-29, parked (no own plan yet). Recorded so it
> isn't lost. **Do not fold into this plan** — different transport/tool/connectivity.

The other half of "track the people associated with the van over whatever link is
available": **off-grid → LoRa/Meshtastic (this plan); on-grid → the phone reports its
own GPS over IP (cell/data).** Likely shape if revived:

- **OwnTracks** (self-hosted, iOS/Android) publishing location over MQTT → reuse the
  existing mosquitto + `mqttbus/ingest.py` bus (a phone is just another topic).
- **Collector = the always-on home site**, reached privately over the WireGuard VPN;
  the van reads/syncs when its uplink to home is up (the van is too often off-grid to
  be the live collector).
- **Storage** per-source (`phone_positions`, like drone/mesh); converge only at a
  shared map "tracked entities" overlay (the same overlay the mesh nodes use).
- **Open:** live vs. history intent; per-source table vs. a generic `tracked_entities`.
- **Caveat:** needs a phone→collector path, so van + phone both off-grid is LoRa-only
  — an argument for having both transports.
