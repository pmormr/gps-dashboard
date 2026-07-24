# Broadcast Dashboard Plan

> Contextualized 2026-07-24 from the parked idea seed (originally
> `events-dashboard-plan.md`; **renamed** to avoid colliding with the places-tier
> `place_events` sense of "events"). An interactive **Broadcast** tab in this app
> that centralizes **config + secrets + live status** for every MediaMTX feed on
> both hubs — the grab-and-go replacement for the static field quick-ref.
>
> Related: `streaming-platform-plan.md` (van PtP-camera ingest/OBS side),
> `cameras-plan.md` (the van Dahua fleet, whose hub paths this also surfaces),
> `.claude/modules/radio.md` R10 (the shared MediaMTX hub), and `paul-network-docs`
> → `events/2026-hillclimb-quickref.md` (the markdown this replaces),
> `events/2026-hillclimb.md`, `cloud/devices/vps202051.md` (cloud hub + Caddy).

## Why

Event-day feed config lives in a **static markdown quick reference** — every path's
send-side settings, OBS pull URL, and secret, maintained by hand. On race day it's
cumbersome (hunt for a URL/passphrase mid-panic) and it can't show **live status**.
The goal is one interactive view that answers, fast: *"phone1 lost its config — what
do I paste into the app?"*, *"is it up?"*, *"why is the audio erroring?"*

## The two hubs (the fact that shapes everything)

The dashboard runs on the **van** Pi `pmpi1` (`192.168.42.178`), frequently off-grid.

| Hub | Where | Control API | WebRTC | From the dashboard |
|---|---|---|---|---|
| **Van** — MediaMTX on `pmpi1` | van LAN | `127.0.0.1:9997` **on** | **on** (`:8889`) | live status **and** WHEP thumbnails already reachable (`/api/mediamtx`, `whep.ts`) |
| **Cloud** — MediaMTX on `vps202051` / `ovh.pmormr.com` | public internet | **off**, `:9997` not exposed | **off** | needs new plumbing — see B7 |

The seed framed cloud reachability around the home→OVH WireGuard tunnel (Graylog-scoped);
that's a red herring for *this* app. The van reaches `ovh.pmormr.com` over the **public
internet** when online — the real gap is that the cloud control API is `api: false` and
only `8890`/`1935`/`8554` are open, so there's nothing to read yet.

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
  codec pin), and `notes` (the gotchas: streamid `&` truncation, RTMP query-string auth,
  mute-drone-audio).

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
  Only the *live* status dots depend on a hub being reachable. Grab-and-go never depends
  on network health.

- **B5 — Van live status reuses the existing control API.** Refactor
  `api/routes/status_mediamtx.py`'s `_paths`/`_normalize_paths` (the `127.0.0.1:9997`
  fetch+normalize) into a shared **`common/mediamtx.py`** client used by both
  `/api/mediamtx` (Diagnostics) and `/api/broadcast/status`. No second hub query path.

- **B6 — Codec-mismatch signal comes from the journal, not just `tracks`.** Under
  `alwaysAvailable`, a mismatched publisher is **rejected** but the path keeps serving the
  STANDBY loop **at the pinned codec** — so the control API's `tracks` shows the pin, not
  the reject. The authoritative "audio error" signal is the mediamtx journal line
  `wants [X] but stream expects [Y]`. **Van:** tail local `journalctl -u mediamtx` (via
  `sudo -n`, the pattern `status_syslog.py` already uses) for recent publish/reject events,
  surfaced inline per feed. **Cloud:** the small OVH status responder (B7) reads *that* box's
  own `journalctl -u mediamtx` locally and folds the recent publish/reject events into the
  JSON Caddy serves — so the van gets cloud reject reasons over HTTP, **no SSH**. **Verify empirically**
  against the live van hub how the control API distinguishes a *live publisher* from the
  STANDBY source (`source.type` vs the static source, `bytesReceived` deltas) — the
  "is it actually up" dot depends on this.

- **B7 — Cloud status via a lightweight authed HTTP status API (not SSH).** Chosen
  deliberately over any SSH-based pull: OVH gets **constant SSH brute-force attempts**, and
  the goal is to lock SSH to the **house only** — so the van's *runtime* cloud-status path
  must be HTTP, independent of SSH (SSH stays a house-only admin/provisioning path). On OVH:
  turn the control API **on but localhost-only** (`api: yes`, `apiAddress: 127.0.0.1:9997` —
  no new public control surface). A **small localhost status responder** (stdlib, ~30 lines,
  bound to `127.0.0.1`) merges the control-API path list **+ a recent `mediamtx` journal
  tail** into one compact JSON — so the van gets cloud reject/mismatch *reasons* over HTTP
  too (this is what removes B6's off-box limitation). **Caddy** (already installed for the
  box's planned reverse-proxy role — this becomes its first site + opens UFW 443/ACME) fronts
  that responder read-only with **token/basic auth** over HTTPS. Van env adds
  `GPS_BROADCAST_CLOUD_URL` + `GPS_BROADCAST_CLOUD_TOKEN`; `/api/broadcast/status` fetches it
  **timeout-guarded**, and off-grid/unreachable degrades to `cloud: {reachable: false}` — the
  normal resting state, **not** a failure (same posture as the on-demand-idle handling in
  `status_mediamtx.py`). Fits the box's minimal-exposure, previously-compromised stance
  (read-only, authed, control API **and** responder both bound to localhost; Caddy is the
  only public surface and it can't reach SSH). Minimal fallback if we defer the journal part:
  Caddy proxies the raw `/v3/paths/list` (status without reject reasons). Documented in
  `vps202051.md`.

- **B8 — The registry generates the van paths block.** `tools/gen_mediamtx_paths.py`
  renders the van broadcast paths (`radio`, `drone1/2`, `cam1`–`cam4`) from the registry
  into `deploy/mediamtx.yml` between sentinel markers, **preserving** the hand-written
  header and the Dahua `${GPS_DAHUA_PASSWORD_URLENC}` proxy block (cameras-owned, left
  as-is). A test asserts the committed block equals the registry render (drift guard). This
  kills the seed's "three drifting copies" for the van side. The **cloud** `mediamtx.yml`
  stays hand-maintained on OVH (different box/repo) — the registry *documents* it and drives
  the mismatch check's expected pins, but doesn't write it (Phase 5 could generate the
  private quick-ref from the registry to close the last copy).

- **B9 — Live thumbnails: van feeds only.** Van WebRTC is on → reuse `whep.ts` + the
  `Cameras.svelte` thumbnail pattern. Cloud `webrtc: false` → cloud feeds show **status
  only**, no thumbnail. Enabling cloud WebRTC for thumbnails is a separate exposure call —
  deferred (Phase 5).

- **B10 — Van hub stays cloud-phone-free.** Phones are on cellular and can't reach the van
  LAN; a van-LAN phone path would defeat the cellular-rendezvous purpose of the cloud hub.
  Leave phones cloud-only (answers the seed's open question).

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
    browser_url=None,   # cloud webrtc off
    notes=['Field apps: streamid is ONLY publish:...:<key> — a stray & truncates it'],
)
```

`GET /api/broadcast/feeds` renders this with env values interpolated → the config-reference
payload. `GET /api/broadcast/status` merges live control-API state + journal events onto it.

## Phases

### Phase 0 — Feed registry + secret plumbing (backbone)
- [ ] `broadcast/feeds.py`: `Feed`/`SendSpec` dataclasses + `FEEDS` covering both hubs.
- [ ] `render_feeds(env)` — interpolate `${ENV_KEY}` placeholders → finished payload (pure, env dict in).
- [ ] `/etc/default/gps-broadcast` (Pi, root-600, gitignored) + `deploy/gps-dashboard.service` gains `EnvironmentFile=-/etc/default/gps-broadcast`. Document the file in CLAUDE.md (Deployment) + the manual-install carve-out.
- [ ] Tests: registry integrity (required fields, `expected_tracks` parse, **no secret literals** in the module), render interpolation.

### Phase 1 — Config reference view (MVP grab-and-go)
- [ ] `api/routes/broadcast.py`: `GET /api/broadcast/feeds` (reads env server-side).
- [ ] `web/src/views/Broadcast.svelte` + `web/src/lib/broadcast.ts` (types, copy-to-clipboard, grouping) + `web/src/lib/api.ts` client.
- [ ] Tab wiring in `routes.ts` (B1). UI: grouped by `slot_group` (PtP Cameras · Phones · Drones · Radio · Security B-roll); each feed a card with copy-buttons for the single-URL send string + fielded host/port/streamid/passphrase + OBS read URL, plus expected pins and the notes/gotchas.
- [ ] Build `static/dist/`, deploy, on-device verify (the panic-scenario walk).

### Phase 2 — Van live status + codec-mismatch
- [ ] `common/mediamtx.py`: shared control-API client; refactor `status_mediamtx.py` onto it (B5).
- [ ] `GET /api/broadcast/status` (van section): live path state merged onto the registry; expected-vs-live tracks + mismatch flag; recent `mediamtx` journal publish/reject events via `sudo -n` (B6).
- [ ] View: status dots (live/standby/idle/offline), viewers/bytes, mismatch badge + inline reject line; van-feed WHEP thumbnails (reuse `whep.ts`).
- [ ] Verify against the live van hub, incl. the live-publisher-vs-STANDBY distinction (B6).
- [ ] Tests: status-merge/mismatch pure logic; `/api/broadcast/status` via Flask client (mocked control API).

### Phase 3 — Cloud live status (lightweight HTTP status API) — off-repo OVH work
- [ ] OVH `mediamtx.yml`: `api: yes`, `apiAddress: 127.0.0.1:9997` (localhost only).
- [ ] Small localhost status responder on OVH: control-API path list **+** recent `mediamtx` journal events → one JSON (stdlib; bound to `127.0.0.1`; systemd unit).
- [ ] Caddy: read-only, token/basic-authed HTTPS route → the responder (first Caddy site; UFW 443; ACME for `ovh.pmormr.com`). Document in `vps202051.md` + `reverse-proxy-setup.md`.
- [ ] Van env: `GPS_BROADCAST_CLOUD_URL`, `GPS_BROADCAST_CLOUD_TOKEN`.
- [ ] `/api/broadcast/status`: cloud section, timeout-guarded; unreachable → `cloud.reachable=false` (not a failure). View shows cloud dots + tracks/viewers + reject reasons (no thumbnail).
- [ ] Update the network-docs event pages.

### Phase 4 — Registry-driven van config generation
- [ ] `tools/gen_mediamtx_paths.py`: registry → van `deploy/mediamtx.yml` broadcast paths (sentinel markers; preserve header + Dahua block) (B8).
- [ ] Drift-guard test: committed block == registry render.

### Phase 5 — Deferred
- [ ] Generate the `paul-network-docs` quick-ref from the registry (+ a local secrets file) — kills the last drifting copy.
- [ ] Enable cloud WebRTC → cloud thumbnails (exposure decision).
- [ ] Optional in-app secret editor, if SSH env-file maintenance proves annoying.
- [ ] Surface the day-of YouTube stream key (an env slot vs. leave it in OBS/password-manager).

## Reusable building blocks (already in-repo)
- `api/routes/status_mediamtx.py` → the control-API fetch/normalize to lift into `common/mediamtx.py`.
- `web/src/lib/whep.ts` + `web/src/views/Cameras.svelte` — WHEP live thumbnails (van feeds).
- `routes.ts` `NAV`/`routes`/`PHONE_PRIMARY_TABS` — top-level tab wiring.
- `updater/chunks.py` — the declarative-registry precedent for `broadcast/feeds.py`.
- `/etc/default/gps-dahua` + `sensor-dahua.service` `EnvironmentFile` — the env-secret pattern.
- `api/routes/status_syslog.py` — the `sudo -n` journal-read pattern for B6.

## Open items
- **SSH lockdown to the house** — the HTTP status API (B7) is the enabler; sequence the OVH SSH-firewall change *after* the status endpoint is verified, so cloud status never depends on SSH. Track in `vps202051.md`.
- **Live-publisher-vs-STANDBY** control-API distinction — verify against the live hub (B6).
- **Naming** — file renamed `events-dashboard-plan.md` → `broadcast-dashboard-plan.md`; flag if "Broadcast" as the tab label should differ.
