# Broadcast — event-day feed config + two-sides live status wall

The **Broadcast** tab (📡, `/broadcast`, a permanent top-level tab) centralizes **config
+ secrets + live status** for every MediaMTX feed across the van and cloud media hubs.
It's the grab-and-go replacement for the hand-maintained field quick-ref, plus a
broadcaster-style **monitor wall**. Built for live-streaming events (first use: the
Mount Equinox Hill Climb, Aug 2026 — multi-camera + phone + drone feeds cut in OBS),
but the tab is permanent, not event-scoped.

**Why two halves per feed (the whole point).** Each feed is decoupled: OBS pulls a
MediaMTX path that is *always* serving at a pinned codec (`alwaysAvailable` = a STANDBY
loop when no real publisher is attached), so OBS never sees the underlying camera/phone
drop — MediaMTX is the stable intermediary (fixes last year's OBS-loses-its-source pain).
The cost is the exact state the wall must surface: **ingest** (is a real source sending?)
and **egress** (is OBS pulling, is data flowing?) are independent, and the healthy one
masks the dead one. The failure mode the wall exists to catch: camera dies → path serves
STANDBY → **egress looks perfectly healthy, OBS broadcasts a placeholder unaware.**

## The two hubs

The cloud hub **mirrors** the van hub (same paths, same STANDBY/pin design); the only
deltas are auth + reachability. One registry (`broadcast/feeds.py`) describes both.

| Hub | Where | Control / logs / snapshots | Video (ingest/egress) |
|---|---|---|---|
| **Van** — `pmpi1` (`192.168.42.178`) | van LAN, often off-grid | localhost control API `127.0.0.1:9997` + local `journalctl` | LAN, **no auth** |
| **Cloud** — `vps202051` / `ovh.pmormr.com` | public internet | over the **WG control tunnel** (B7): control API + agent bound to the WG iface | **public, authed** |

**Video stays public and untunneled on both hubs** (deliberate — keeps the public
rendezvous target, avoids tunneling multi-Mbps video). Only the *control plane* — status
polling, log tails, JPEG snapshots (a trickle) — rides the tunnel.

## Code map

| Concern | Code |
|---|---|
| **Registry** (source of truth, both hubs) | `broadcast/feeds.py` — `Feed`/`SendSpec` + `FEEDS`; `render_feeds(env)` |
| **Shared control-API client** | `common/mediamtx.py` — `PathState` + `fetch_paths` (also backs `/api/mediamtx`, B5) |
| **Two-sides status** (pure) | `broadcast/status.py` — `ingest_state`/`codec_badge`/`feed_status` |
| **Snapshotter** (localhost RTSP → JPEG) | `broadcast/snapshots.py` — `SnapshotManager` |
| **Cloud agent** (off-repo, on the vps) | `broadcast/cloud_agent.py` + `broadcast/cloud_agent.service` |
| **Routes** | `api/routes/broadcast.py` — `/api/broadcast/{feeds,status,snapshot,logs}` |
| **Frontend** | `web/src/views/Broadcast.svelte` (Wall/Config toggle) + `BroadcastWall.svelte` + `BroadcastConfig.svelte` + `web/src/lib/broadcast.ts` (WHEP expand via `whep.ts`) |
| **Config generation** | `tools/gen_mediamtx_paths.py` — registry → van `deploy/mediamtx.yml` (B8) |

## Registry & config reference (B2/B3/B4)

`broadcast/feeds.py` is a declarative `FEEDS` list (the `updater/chunks.py` /
`api/sensor_schema.py` pattern): one `Feed` per `(hub, path)` — that pair is the
identity (`drone1` legitimately exists on both hubs). Per feed: transport/role, send
config, `obs_read`, `standby`, `expected_tracks` (the codec pin), `notes`. Single-URL
send forms + the referenced env-key set are **derived** (`render_feeds`/`env_keys`), not
stored, so fielded and single-URL views can't drift.

**Secrets never live in the repo** (it's a *public* GitHub repo): the registry holds only
`${ENV_KEY}` placeholders; `render_feeds` interpolates them **server-side** from the
process env into copy-ready strings for the trusted LAN. A missing key is left literal +
reported in `missing_secrets` (a visible "secret not set" signal). The config reference is
**fully local/offline** (B4) — present even with both hubs down, which is when you need it;
only *live* status/snapshots/logs need a hub reachable. Secrets are SSH-edited in the Pi
env file (read-only in the UI); the van hub has none (trusted LAN). A `tests/` guard
(`test_broadcast_feeds.py`) fails on any secret-shaped literal in the module.

## Two-sides status model (B6)

The whole status surface derives from `/v3/paths/list` — **no journal parsing**.

- **Ingest** = `ingest_state`: `live` / `standby` / `idle`. The live discriminator is
  **`source.id` (non-empty), NOT `source.type`** — an idle on-demand/STANDBY path reports
  its *configured* `source.type` (`rtspSource`) with an **empty** id; only a real publisher
  carries a connection id (`PathState.source_connected`). `standby` = an `alwaysAvailable`
  path that is `ready` with no live source.
- **Egress** = `readers` + `bytes_sent` deltas (the client computes throughput across polls).
- **Codec badge** = live `tracks` vs `expected_tracks`, **gated to `ingest=='live'`** — a
  STANDBY loop serves its own tracks (the drone loop even has an audio track the video-only
  pin lacks), so comparing the placeholder to the real-source pin false-flags every idler.
- **`danger`** = `ingest=='standby'` **and** `readers>0` — the masked failure the wall exists
  for. **Self-reader discount:** the wall's own snapshotter pulls over RTSP and the hub counts
  it as a reader; the status route subtracts the hub's active snapshot workers per-path
  (`SnapshotManager.active_paths()` van; the agent's `/active` cloud) or it false-positives
  `danger`.

Hub-agnostic: without STANDBY a dead ingest just drops `ready` (honest); `standby` only
arises on `alwaysAvailable` feeds.

## Cloud plane over WireGuard (B7)

Direct van↔cloud WG peer: the van (behind Starlink CGNAT, no public inbound) **dials out**
to the vps's fixed WG endpoint on a high UDP port (`pmpi1 wg-cloud 10.9.9.2` → vps
`wg1 10.9.9.1:48291`). WireGuard is **scan-invisible**, so there's no public status surface
at all (no Caddy/ACME/443). The cloud MediaMTX control API, the agent, and the log endpoint
**bind to the WG iface** (`10.9.9.1`), not the public internet.

- **Direct, not a van→rex→cloud two-hop** (re-confirmed after a van↔rex site tunnel landed):
  keeps this control tunnel single-purpose (the vps's Graylog-backhaul peer AllowedIPs stays
  pristine), one hop, independent of home being up. Video is never tunneled in *any* design.
- **Degrade:** tunnel down (off-grid / driving on flaky Starlink) → `cloud.reachable: false`,
  the normal resting state, **not** a failure. `/api/broadcast/status` fetches the cloud
  control API timeout-guarded (2.5 s — a down tunnel must fail fast, not hang the poll).
- **The cloud agent** (`broadcast/cloud_agent.py`) is an **off-repo manual install** on the
  vps (`/opt/broadcast-agent`, user `bcagent`, unit `broadcast-agent.service`, secret RTSP
  base in `/etc/default/broadcast-agent`) — a stdlib `ThreadingHTTPServer` reusing the tested
  `SnapshotManager`, serving `/snapshot/<path>` · `/logs` · `/active` · `/health` on
  `10.9.9.1:9998`. Its `.service` lives at `broadcast/cloud_agent.service` but is **kept OUT
  of `deploy/`** so the Pi deploy hook never installs it here.
- **SSH lockdown:** with control on the tunnel, public OVH `:22` was removed — SSH is now
  tunnel-only (rex `wg0`, van jump-host `ssh -J pmpi1 ubuntu@10.9.9.1`, OVH KVM backstop).
  Details in `paul-network-docs` `cloud/devices/vps202051.md`.

## Snapshots (B9) & logs (B11)

**Snapshots** — decode where the video already lives, ship only pixels: one **ffmpeg per
actively-viewed feed** pulls `rtsp://127.0.0.1:8554/<path>`, downscales to ~320 px wide
(~20 kB), writes a rolling latest-frame JPEG (~0.5 fps) to tmpfs (`/dev/shm`). Workers are
lazily started when a tile asks and reaped after an idle TTL (bounded to feeds *on screen*,
not poll rate — the Cameras "self-schedule + cache" lesson), respawn-backoff'd so a
never-ready feed can't thrash ffmpeg. Wall tiles poll `/api/broadcast/snapshot/<name>`
(202 while warming). The wall uses snapshots uniformly for **both** hubs; the van adds live
**WHEP** 720p on click-to-expand (H.264 sub-streams, `whep.ts`). Cloud is snapshot-only —
the phones are **H.265**, which browsers can't WHEP (ffmpeg decodes it fine for the JPEG).
When a source drops, the snapshot shows the STANDBY card — visually confirming the B6
masked-ingest state.

**Logs** — the raw diagnostic escape hatch: `journalctl -u mediamtx`, last-N-lines,
poll-refreshed (van reads locally — the service user is in `adm`, **no sudo**; cloud proxied
through the agent). We don't classify *why* a publish was rejected — the human eyeballs it.

## Config generation (B8)

`tools/gen_mediamtx_paths.py` renders the van's `source: publisher` paths (cam1–4, radio,
drone1/2 — role `publish`/`internal`) into `deploy/mediamtx.yml` between `# >>> BEGIN/END
generated van paths <<<` sentinels; the hand-written header and the Dahua on-demand proxy
block (`${GPS_DAHUA_PASSWORD_URLENC}`, cameras-owned) are preserved verbatim. Pure
`render_block`/`splice` core; `--check` prints a unified diff + exits 1 on drift.
`tests/test_gen_mediamtx_paths.py` asserts committed-block == render, so a `feeds.py` edit
that isn't regenerated fails the suite. **Regenerate + commit before pushing.** The **cloud**
`mediamtx.yml` lives on a different box and stays hand-maintained (the registry documents it
+ drives the codec pins but doesn't write it).

## Traps (durable, verified live)

- **`source.id` (non-empty), not `source.type`, is the "real publisher connected" signal.**
  Idle on-demand/STANDBY paths report a configured `source.type` with an empty id.
- **A STANDBY source reports `source: null`** (not a standby-source object with an id) →
  `source_connected` False → `ingest_state` returns `standby`, no guard needed. (This was the
  one carried-over unknown; resolved live on the cloud hub.)
- **MediaMTX names the AAC track `'MPEG-4 Audio'`** (hyphen + space), *not* `'MPEG4Audio'` —
  the phone codec pin must match exactly. And the badge is gated to a live source (above).
- **The wall's own snapshotter counts as an egress reader** — discount it per-hub or `danger`
  false-positives.
- **Cloud RTSP read is authed** (`obs` cred), unlike the open van LAN — the agent's
  `rtsp_base` injects it; `bcagent` needs `systemd-journal` to read the journal without sudo.
- **The cloud agent's `.service` stays out of `deploy/`** (off-repo install; the hook would
  otherwise try to install it on the Pi).

## Deploy & secrets

- **Van:** secrets in `/etc/default/gps-broadcast` (root-600, hook-skipped), loaded by
  `gps-dashboard.service` `EnvironmentFile`: `GPS_BROADCAST_*` stream keys + the WG-tunnel base
  URLs `GPS_BROADCAST_CLOUD_URL` (control API) / `GPS_BROADCAST_CLOUD_AGENT_URL` (agent). No
  new unit — the tab lives in the existing web app.
- **Cloud:** the agent + its `/etc/default/broadcast-agent` (`BROADCAST_AGENT_RTSP_BASE` =
  `rtsp://obs:<pass>@127.0.0.1:8554`) are off-repo on the vps; MediaMTX there runs `api: yes`
  bound to the WG iface with the van's key allowed.

## Decision anchors (code references these by number)

B1 permanent 12th tab (📡, not in `PHONE_PRIMARY_TABS`) · B2 registry is the data model ·
B3 secrets in the Pi env file, never committed (public repo) · B4 config reference fully
local/offline · B5 one shared `common/mediamtx.py` client both hubs · B6 two-sides status
from the control API (no journal parsing) · B7 cloud control plane over a direct van↔cloud
WG tunnel (video stays public) · B8 registry generates the van `mediamtx.yml` paths · B9
unified JPEG-snapshot previews both hubs · B10 phones stay cloud-only (van LAN unreachable
from cellular) · B11 live raw log panel per hub.

## Deferred / rejected (Phase 5, all dropped 2026-07-27)

- **SSE/streaming log tail** — dropped: the log is a human-eyeball diagnostic; a poll tail is
  indistinguishable, and streaming would double the surface (Flask + the stdlib agent).
- **Cloud WebRTC live-on-expand** — dropped: phones are H.265 (unWHEPable) so it'd help only
  the 2 H.264 drone slots, and it means public WebRTC exposure (video, which B7 keeps off the
  tunnel). Snapshots already cover liveness.
- **Generate the quick-ref sheet from the registry** — dropped: the Broadcast tab is now the
  canonical offline grab-and-go, so the `paul-network-docs` sheet was **thinned to a paper
  fallback pointing at the tab** instead (removing its inline secret copies), rather than
  building cross-repo tooling + a laptop secrets file.
- **In-app secret editor** — rejected on trust-model grounds: a secret-*write* surface on a
  no-auth LAN app (+ a privileged root-600 write helper) isn't worth saving a rare SSH edit.
- **YouTube stream key slot** — dropped: it's OBS's *output* to the final destination, not a
  hub path; it rotates per-broadcast and belongs in the password manager, not the set-once env.

## Related

`radio.md` (R10 — the shared MediaMTX hub), `cameras-plan.md` (the van Dahua fleet whose hub
paths this surfaces + the snapshot pattern B9 generalizes), `streaming-platform-plan.md` (the
van PtP-camera ingest side). Mirror docs: `paul-network-docs` → `events/2026-hillclimb.md`,
`events/2026-hillclimb-quickref.md`, `cloud/devices/vps202051.md`, `van/devices/pmpi1.md`.
