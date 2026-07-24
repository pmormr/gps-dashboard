# Broadcast Dashboard Plan

> Contextualized 2026-07-24 from the parked idea seed (originally
> `events-dashboard-plan.md`; **renamed** to avoid colliding with the places-tier
> `place_events` sense of "events"). An interactive **Broadcast** tab in this app
> that centralizes **config + secrets + live status** for every MediaMTX feed on
> both hubs — the grab-and-go replacement for the static field quick-ref, plus a
> broadcaster-style monitor wall.
>
> **Driving deadline: the Mount Equinox Hill Climb (Vermont), weekend of Aug 8,
> 2026** — ~2 weeks out at shaping. The race day drives the cut: Phases 0–2 are the
> committed must-have; Phase 3 (cloud) is high-value stretch; Phases 4–5 are post-race.
>
> Related: `streaming-platform-plan.md` (van PtP-camera ingest/OBS side),
> `cameras-plan.md` (the van Dahua fleet, whose hub paths this also surfaces, and
> the snapshot-thumbnail pattern B9 generalizes), `.claude/modules/radio.md` R10
> (the shared MediaMTX hub), and `paul-network-docs` →
> `events/2026-hillclimb-quickref.md` (the markdown this replaces),
> `events/2026-hillclimb.md`, `cloud/devices/vps202051.md` (cloud hub + Caddy + WG).

## Why

Event-day feed config lives in a **static markdown quick reference** — every path's
send-side settings, OBS pull URL, and secret, maintained by hand. On race day it's
cumbersome (hunt for a URL/passphrase mid-panic) and it can't show **live status**.
The goal is one interactive view that answers, fast: *"phone1 lost its config — what
do I paste into the app?"*, *"is it up?"*, *"is OBS actually getting real video or a
placeholder?"*, and — glance-level — *"what's on every feed right now?"*

### The always-available design (why every feed has two halves)

Last year the pain was that **OBS's own reconnect/direct-ingest was unreliable** — a
camera hiccup would drop OBS's source and it wouldn't cleanly recover mid-broadcast.
The always-available (STANDBY) path fixes that by making **MediaMTX the stable
intermediary**: OBS pulls one path that is *always* serving at the pinned codec, so
OBS never sees the underlying camera drop — MediaMTX serves a STANDBY loop until the
real publisher returns. That decouples OBS from the flaky camera/phone links.

The cost of that decoupling is the exact thing this dashboard must surface: **each
feed is now two independent halves**, and the healthy-looking one can mask a dead one.

- **Ingest** (publisher → hub): is the real source connected and sending?
- **Egress** (hub → OBS): is OBS pulling, and is video flowing to it?

The dangerous state: camera drops → ingest is dead → the path keeps serving STANDBY →
**egress looks perfectly healthy and OBS never errors** → you're broadcasting a
placeholder loop unaware. That "looked fine, wasn't" case is last year's failure mode,
and making it visible is the point of the status feature (B6).

## The two hubs (the fact that shapes everything)

The dashboard runs on the **van** Pi `pmpi1` (`192.168.42.178`), frequently off-grid.
The cloud hub **mirrors** the van hub (same paths, same STANDBY/pin design) — the only
deltas are that the cloud hub **requires auth** (public internet; the van LAN is
trusted) and is reachable only over the control tunnel (B7). So the registry (B2)
describes **both** from one source of truth; don't read too much into either hub's
*current* transitional config.

| Hub | Where | Control / logs / snapshots | Video (ingest/egress) | Previews |
|---|---|---|---|---|
| **Van** — `pmpi1` | van LAN | localhost control API (`127.0.0.1:9997`) + local `journalctl` | LAN, **no auth** | JPEG snapshot (local) + WHEP live on expand |
| **Cloud** — `vps202051` / `ovh.pmormr.com` | public internet | over the **WG control tunnel** (B7) — control API + log + snapshot bound to the WG iface | **public**, authed | JPEG snapshot over the tunnel (no browser WebRTC) |

**Video stays public and untunneled** on both hubs (deliberate: keeps the public test
target people can rendezvous against, and avoids tunneling multi-Mbps video). Only the
*control plane* — status polling, log tails, snapshot fetches — rides the tunnel.

## Decisions

- **B1 — "Broadcast" tab, permanent, top-level.** Route `/broadcast`, icon 📡, view
  `Broadcast.svelte`, registered in `web/src/lib/routes.ts` (`routes` + `NAV`). This is
  the **12th** tab; **not** in `PHONE_PRIMARY_TABS`, so it folds into the phone "More"
  overflow (desktop sidebar shows all). Name is **Broadcast**, not "Event(s)" — `events`
  already means the places-tier `place_events` (scheduled programs) across the codebase.

- **B2 — A feed registry is the data model.** `broadcast/feeds.py` — a declarative
  `Feed` dataclass + `FEEDS` list describing **every path on both hubs** (radio, `cam1`–
  `cam4`, `phone1`–`phone5`, `drone1`/`drone2`, and the van Dahua `cam-*` B-roll paths).
  Same declarative-source-of-truth shape as `updater/chunks.py` and `api/sensor_schema.py`.
  Committed, public-safe: **structure only, no secret values** (B3). Per feed: `path`,
  `label`, `hub`, `slot_group`, `transport` (srt/rtmp/rtsp-pull/internal), `role`
  (publish/proxy/internal), send config (host, port, streamid template, latency,
  encryption, expected video+audio codec), `secret_ref` env keys, `obs_read` template,
  `browser_url` (van only), `standby` (`alwaysAvailable`) flag, `expected_tracks` (the
  codec pin, drives B6's codec badge), and `notes` (the gotchas: streamid `&` truncation,
  RTMP query-string auth, mute-drone-audio). Because the hubs mirror, most fields are
  shared; `hub` + the auth/reachability deltas are the per-hub variation.

- **B3 — Secrets: Pi env file, never committed (public-repo constraint).**
  `gps-dashboard` is a **public GitHub repo**, so the SRT passphrase and the
  phone/drone/obs creds **cannot** live in it. They go in **`/etc/default/gps-broadcast`**
  (root-600, gitignored), loaded by `gps-dashboard.service` via `EnvironmentFile`, exactly
  like `/etc/default/gps-dahua` (`GPS_DAHUA_PASSWORD`) and `/etc/default/gps-victron`. The
  registry (B2) holds `${ENV_KEY}` placeholders; the feeds API reads env **server-side**
  and returns finished, copy-ready strings to the trusted LAN. This answers the seed's
  "secrets in the UI": **yes, LAN-served, env-sourced, never committed** — consistent with
  the app's no-auth-on-LAN stance and the low-sensitivity-stream-key stance already
  declared in the quick-ref. Entry/rotation is **SSH-editing the env file** (read-only in
  the UI); no in-app write surface. The van hub has **no** secrets (trusted LAN).

- **B4 — The config reference is fully local/offline (robustness invariant).** It's
  served by the local Flask app from local data (registry + env), so a feed's config is
  present **even off-grid, with either hub down** — which is exactly when you need it.
  Only the *live* status/snapshots/logs depend on a hub being reachable. Grab-and-go never
  depends on network health.

- **B5 — One shared control-API client, both hubs.** Refactor
  `api/routes/status_mediamtx.py`'s `_paths`/`_normalize_paths` (the control-API
  fetch+normalize) into a shared **`common/mediamtx.py`** used by `/api/mediamtx`
  (Diagnostics), the van section of `/api/broadcast/status`, **and** the cloud section
  (same client, pointed at the cloud control API over the WG tunnel — B7). No second hub
  query path.

- **B6 — Status model: the two sides of every feed, read from the control API.** The whole
  status surface derives from `/v3/paths/list` (`common/mediamtx.py`) — **no journal
  parsing** for status:
  - **Ingest half:** `source` (its `type` + `id`) + `bytesReceived` deltas. The critical
    lever is distinguishing a *real publisher* (`srtConn`/`rtmpConn`/`webRTCSession`…) from
    the *STANDBY source* — a `ready:true` path whose `source.type` is the static standby,
    not a live connection. That surfaces the "ingest dead, still serving placeholder" trap.
  - **Egress half:** `readers[]` (is OBS/anyone pulling) + `bytesSent` deltas (is video
    actually flowing to them).
  - **Codec badge (free):** live `tracks` vs the registry's `expected_tracks` → a simple
    match/mismatch flag. Cheap, no journal.
  - **MUST verify empirically against the live van hub:** exactly how the control API
    reports live-publisher vs STANDBY (`source.type` string, `bytesReceived` behavior) and
    how the pinned `tracks` read under STANDBY — the ingest dot depends on it. The van hub
    is the cheap test rig (localhost control API); the mismatch/STANDBY reasoning applies to
    both hubs once they mirror.
  Reject *reasons* (why a mismatched publisher was refused) are **not** parsed into
  structured signals — that lives in the raw log panel (B11) instead.

- **B7 — Cloud control plane over a direct van↔cloud WireGuard tunnel (video stays
  public).** Chosen over the seed's public authed-HTTP-status-API idea, and over reusing
  the home↔OVH tunnel:
  - **Video is NOT tunneled** — cloud MediaMTX ingest/egress stay on their public ports
    (keeps the public test target; avoids tunneling multi-Mbps video). Only control/log/
    snapshot traffic (a trickle) rides the tunnel.
  - **Direct van↔cloud WG peer.** The van is behind Starlink CGNAT (no public inbound), so
    the **van dials out** to the cloud's fixed WG endpoint on a **high UDP port**.
    WireGuard is **scan-invisible** (silent to any packet without a valid key), so it
    satisfies the security-through-obscurity goal *better* than a high-port HTTPS endpoint —
    and there is **no public status surface at all** (no Caddy site, no ACME, no 443/TLS
    decision; all of that machinery is eliminated).
  - **Cloud services bind to the WG interface**, not the public internet: MediaMTX control
    API (`api: yes`, WG-iface address), the snapshotter (B9), and the log endpoint (B11).
    Reached from the van at their WG addresses. A token is optional defense-in-depth — the
    tunnel is the real trust boundary.
  - **Direct, not two-hop** (rejected van→home→cloud over the existing home↔OVH tunnel):
    one hop, doesn't depend on home being up or the van reaching home.
  - **Degrade posture unchanged:** tunnel down (van driving on flaky Starlink, or off-grid)
    → `cloud.reachable: false`, the normal resting state, **not** a failure — same as the
    on-demand-idle handling in `status_mediamtx.py`. Van env adds `GPS_BROADCAST_CLOUD_URL`
    (the WG-side base); `/api/broadcast/status` fetches it **timeout-guarded**. Event
    connectivity is good (fixed Starlink with sky view / summit visitor-center uplink), so
    the tunnel is solid when it matters.
  - **Enables the SSH lockdown** (an open item): with control on the tunnel, public SSH to
    OVH can be firewalled to **tunnel + house only**, killing the constant brute-force
    surface. Sequence that firewall change *after* the tunnel + status are verified, so
    cloud status never depends on the change landing. Documented in `vps202051.md`.

- **B8 — The registry generates the van paths block (post-race).** `tools/gen_mediamtx_paths.py`
  renders the van broadcast paths (`radio`, `drone1/2`, `cam1`–`cam4`) from the registry
  into `deploy/mediamtx.yml` between sentinel markers, **preserving** the hand-written
  header and the Dahua `${GPS_DAHUA_PASSWORD_URLENC}` proxy block (cameras-owned, left
  as-is). A test asserts the committed block equals the registry render (drift guard). This
  kills the seed's "three drifting copies" for the van side. The **cloud** `mediamtx.yml`
  stays hand-maintained on OVH (different box/repo) — the registry *documents* it and drives
  the codec badge's expected pins, but doesn't write it (Phase 5 could generate the private
  quick-ref from the registry to close the last copy). Low urgency — deferred past race day.

- **B9 — Unified JPEG-snapshot previews for both hubs (a monitor wall).** The aspiration is
  a broadcaster's TV wall — every feed tiled with both-sides health + a glance preview — so
  you decide what to route into OBS's production feed. Mechanism: **decode where the video
  already lives, ship only pixels-as-JPEG.** Cloud feeds are **H.265** (phones), which
  browsers **cannot** decode over WHEP — so public WebRTC previews were a dead end for the
  phones regardless of exposure. Snapshots sidestep that entirely and generalize the
  Cameras-plan snapshot pattern:
  - On each hub, a lightweight **ffmpeg per active feed** pulls from **localhost** RTSP
    (`rtsp://127.0.0.1:8554/<path>`), downscales, writes a latest-frame JPEG (~0.5 fps) to
    tmpfs. Started when the wall opens, stopped after an idle TTL (bounds ffmpeg processes to
    active feeds, not polls — the Cameras "self-schedule + cache" lesson).
  - A tiny endpoint serves that JPEG: van locally; cloud bound to the WG interface (B7).
    Tiles poll it, **self-scheduled per tile** (a shared timer starves slow tiles).
  - **Impact (analyzed):** ~320×180 JPEG ≈ 20 KB; ~6 cloud tiles @ 2 s ≈ **0.5 Mbps
    aggregate**, on-demand (only while the Broadcast view is foregrounded) — ~5–10× lighter
    than continuous WebRTC video and no transcode-to-stream. Cloud CPU: one H.265 frame per
    feed per ~2 s is negligible; throttle fps/tile-count if the VPS strains.
  - **The wall uses snapshots uniformly for both hubs** (consistent, one mechanism); the
    van's built **WHEP** live tiles (`whep.ts`, H.264 sub-streams) are used on
    **click-to-expand** for live motion. Cloud expand stays snapshot (faster refresh) until/
    unless cloud WebRTC is enabled (Phase 5 exposure call).
  - **Bonus:** when a source drops, the snapshot shows the **STANDBY card / frozen frame** —
    visually confirming the B6 "ingest dead, egress still serving placeholder" state. Preview
    and status reinforce each other on exactly last year's failure mode.

- **B10 — Van hub stays cloud-phone-free.** Phones are on cellular and can't reach the van
  LAN; a van-LAN phone path would defeat the cellular-rendezvous purpose of the cloud hub.
  Leave phones cloud-only (answers the seed's open question).

- **B11 — Live raw log panel per hub (the diagnostic escape hatch).** We do **not** try to
  fully classify what's wrong — just make the hub's `journalctl -u mediamtx` **visible live**
  so a human can eyeball it (publish/reject events, connection churn, codec refusals). Van:
  read locally via `sudo -n` (the `status_syslog.py` pattern). Cloud: a tiny recent-lines
  endpoint bound to the WG interface (B7), fetched over the tunnel. First cut = "last N
  lines, poll-refreshed" (a live-*ish* tail); true SSE streaming is a Phase-5 polish. This
  replaces the seed's fragile journal-parsing — raw logs cover the "why" without pretending
  to structure it.

## Data model sketch (`broadcast/feeds.py`)

```python
Feed(
    path='phone1', label="Paul's phone", hub='cloud', slot_group='phones',
    transport='srt', role='publish',
    send=SendSpec(host='ovh.pmormr.com', port=8890,
                  streamid='publish:phone1:publisher:${GPS_BROADCAST_PHONE_PUB}',
                  passphrase='${GPS_BROADCAST_SRT_PASSPHRASE}',
                  latency_ms=2000, encryption='AES-128'),
    expected_tracks=['H265', 'AAC 44100/2'], standby=True,
    obs_read='rtsp://obs:${GPS_BROADCAST_OBS_READ}@ovh.pmormr.com:8554/phone1',
    browser_url=None,   # cloud webrtc off — preview is a snapshot over the tunnel (B9)
    notes=['Field apps: streamid is ONLY publish:...:<key> — a stray & truncates it'],
)
```

`GET /api/broadcast/feeds` renders this with env values interpolated → the config-reference
payload. `GET /api/broadcast/status` merges live control-API two-sides state (B6) onto it.
Snapshots (B9) and logs (B11) are separate endpoints per hub.

## Phases

### Phase 0 — Feed registry + secret plumbing (backbone) · race-day ✅
- [x] `broadcast/feeds.py`: `Feed`/`SendSpec` dataclasses + `FEEDS` covering both hubs. Single-URL forms + referenced env keys are **derived** (`render_feed`/`env_keys`), not stored, so fielded/single-URL can't drift and there's no hand-kept secret-ref list.
- [x] `render_feeds(env)` — pure `${ENV_KEY}` interpolation; a missing key is left literal + reported in per-feed + top-level `missing_secrets` (visible "secret not set" signal).
- [x] `/etc/default/gps-broadcast` (Pi, root-600) + `deploy/gps-dashboard.service` `EnvironmentFile=-/etc/default/gps-broadcast` (leading `-` optional — van feeds need no secrets). Documented in CLAUDE.md (manual-install carve-out). `broadcast` added to pyproject packages/isort/mypy-strict.
- [x] Tests (`tests/test_broadcast_feeds.py`, 15): registry integrity (enums, `(hub,path)` unique, publish⇒send, standby=cloud-only), **no-secret-literals guard** (no hex-run in the module; doesn't embed the secrets either), render interpolation + derived SRT/RTMP single-URLs.

### Phase 1 — Config reference view (MVP grab-and-go) · race-day ✅
- [x] `api/routes/broadcast.py`: `GET /api/broadcast/feeds` (reads env server-side); registered in `api/app.py`. Tests in `tests/test_broadcast_api.py` (3).
- [x] `web/src/views/Broadcast.svelte` + `web/src/lib/broadcast.ts` (types, grouping, `copyText` with an **insecure-context `execCommand` fallback** — the app is plain-HTTP LAN, so `navigator.clipboard` is unavailable there) + `web/src/lib/api.ts` client.
- [x] Tab wiring in `routes.ts` (B1 — 12th tab, 📡, not in `PHONE_PRIMARY_TABS` → folds into phone "More"). UI: grouped by `slot_group`; each feed a card with copy-buttons for the single-URL + fielded host/port/streamid/passphrase + OBS read, hub/standby/on-demand badges, expected pins, notes/gotchas.
- [x] Built `static/dist/`; verified locally via playwright (render + working copy feedback + 0 console errors). On-device deploy verify next.

### Phase 2 — Van live status (two-sides) + snapshots + logs · race-day
- [ ] `common/mediamtx.py`: shared control-API client; refactor `status_mediamtx.py` onto it (B5).
- [ ] `GET /api/broadcast/status` (van section): two-sides state merged onto the registry — ingest (`source.type` + `bytesReceived` delta, live-vs-STANDBY), egress (`readers` + `bytesSent` delta), codec badge (`tracks` vs `expected_tracks`) (B6).
- [ ] Van snapshotter: ffmpeg localhost RTSP → tmpfs JPEG (idle-TTL); serve endpoint (B9).
- [ ] Van live-log endpoint: `sudo -n journalctl -u mediamtx` recent lines (B11).
- [ ] View: monitor-wall tiles (two-sides dots + throughput + codec badge + snapshot), click-to-expand → WHEP live (van), raw log panel.
- [ ] **Verify against the live van hub** — the live-publisher-vs-STANDBY distinction and STANDBY `tracks` behavior (B6, the empirical must-verify).
- [ ] Tests: status-merge/two-sides pure logic; `/api/broadcast/status` via Flask client (mocked control API).

### Phase 3 — WG control tunnel + cloud status/snapshots/logs · race-day stretch (off-repo OVH + van net)
- [ ] Stand up the **direct van↔cloud WireGuard peer**: cloud = fixed endpoint on a high UDP port, van dials out (CGNAT); allowed-IPs scoped to the control plane only.
- [ ] OVH `mediamtx.yml`: `api: yes`, control API bound to the **WG interface** (not public).
- [ ] OVH: cloud snapshotter (B9) + log endpoint (B11), both bound to the WG interface (systemd units; stdlib/ffmpeg).
- [ ] Van env: `GPS_BROADCAST_CLOUD_URL` (WG base; optional token). `/api/broadcast/status` cloud section, timeout-guarded; unreachable → `cloud.reachable=false` (not a failure). Wall shows cloud tiles (snapshots + two-sides + logs); no browser WebRTC.
- [ ] Verify cloud status/snapshots/logs over the tunnel end-to-end.
- [ ] **Then** sequence the public-SSH → tunnel+house firewall lockdown (after status verified). Update `vps202051.md` + the network-docs event pages.

### Phase 4 — Registry-driven van config generation · post-race
- [ ] `tools/gen_mediamtx_paths.py`: registry → van `deploy/mediamtx.yml` broadcast paths (sentinel markers; preserve header + Dahua block) (B8).
- [ ] Drift-guard test: committed block == registry render.

### Phase 5 — Deferred
- [ ] True SSE/streaming log tail (vs poll-refreshed lines).
- [ ] Enable cloud WebRTC → cloud live-on-expand (exposure decision; H.264 feeds only).
- [ ] Generate the `paul-network-docs` quick-ref from the registry (+ a local secrets file) — kills the last drifting copy.
- [ ] Optional in-app secret editor, if SSH env-file maintenance proves annoying.
- [ ] Surface the day-of YouTube stream key (an env slot vs. leave it in OBS/password-manager).

## Reusable building blocks (already in-repo)
- `api/routes/status_mediamtx.py` → the control-API fetch/normalize to lift into `common/mediamtx.py` (B5).
- `web/src/lib/whep.ts` + `web/src/views/Cameras.svelte` — WHEP live tiles (van click-to-expand) + the per-tile self-scheduled refresh + server-side snapshot-downscale pattern B9 generalizes.
- `routes.ts` `NAV`/`routes`/`PHONE_PRIMARY_TABS` — top-level tab wiring.
- `updater/chunks.py` — the declarative-registry precedent for `broadcast/feeds.py`.
- `/etc/default/gps-dahua` + `sensor-dahua.service` `EnvironmentFile` — the env-secret pattern.
- `api/routes/status_syslog.py` — the `sudo -n` journal-read pattern for B11.
- The home↔OVH WireGuard config in `paul-network-docs` (`rex-edge.md` / `vps202051.md`) — the WG precedent B7 adds a van peer alongside.

## Open items
- **Live-publisher-vs-STANDBY** control-API distinction — the one hard empirical unknown;
  verify against the live van hub before finalizing the ingest dot (B6, Phase 2).
- **SSH lockdown to the house** — the WG tunnel (B7) is the enabler; sequence the OVH
  SSH-firewall change *after* cloud status is verified over the tunnel. Track in `vps202051.md`.
- **Naming** — file renamed `events-dashboard-plan.md` → `broadcast-dashboard-plan.md`; flag
  if "Broadcast" as the tab label should differ.
