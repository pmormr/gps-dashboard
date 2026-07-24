# Events Dashboard — Idea Seed

> **SEED / parked 2026-07-24 — NOT a worked plan.** Captured from a live-streaming
> session so the idea isn't lost. Needs a proper contextualization pass in this repo
> (architecture, scope, phases) before any build. Related: `streaming-platform-plan.md`
> (ingest/OBS side), and `paul-network-docs` → `events/2026-hillclimb-quickref.md` (the
> static reference this would replace) + `cloud/devices/vps202051.md` (cloud hub config).

## Why

The event config lives in a **static markdown quick reference** (send-side settings, OBS
pull URLs, secrets for every feed). It's getting cumbersome to use on event day, and it
can't show **live status**. Want an interactive **Event** view in this dashboard that
centralizes config + status + details for every feed in one place.

## What it should centralize (the useful bits)

1. **Config reference, interactive.** Per feed: publish/send config, OBS pull URL, secrets,
   with copy-to-clipboard. An interactive replacement for the markdown quick-ref.
2. **Live status across both hubs.** Online vs offline (publisher connected vs serving the
   STANDBY slate), tracks, reader/viewer count, bytes — the `status_mediamtx.py` data,
   surfaced per feed.
3. **Codec / alwaysAvailable state + mismatch alerts.** Each path is now hard-pinned to a
   codec (see below); surface the pinned codec vs the actual publisher and **flag a
   hard-reject mismatch** — that class of failure ate an hour of debugging this session and
   is exactly what a glanceable dashboard would catch instantly.

## Context a contextualizing agent needs (facts from this session)

- **Two hubs, and reaching the cloud one is the central question.**
  - Van hub `pmpi1` — MediaMTX control API on `127.0.0.1:9997`, already queried by
    `api/routes/status_mediamtx.py`. Paths: `radio`, `drone1/2`, `cam1-4` (hill-climb SRT),
    `cam-*` Dahua. **No phone paths — phones are cloud-only** (confirm if that should change).
  - Cloud hub OVH `ovh.pmormr.com` (`158.69.222.243`) — phones `phone1-5`, drones `drone1/2`.
    Its control API is **off**, and it's only reachable from home over a WireGuard tunnel that
    is currently **scoped to Graylog (`10.1.100.224:514`) only**. So the dashboard (on pmpi1)
    can't see cloud status yet. Options to weigh: widen the tunnel to the OVH API, have OVH
    push status somewhere, or aggregate from the **OBS laptop** (which already reaches both
    hubs). This is the main architecture decision.
- **`alwaysAvailable` (added on OVH 2026-07-24).** Each path loops an offline STANDBY segment
  so the OBS reader **stays connected across a publisher drop** instead of disconnecting. It
  **hard-pins each path to an exact codec + audio config** — a publisher that doesn't match is
  rejected, and the journal prints `wants [X] but expects [Y]` / `sampleRate=… does not match`.
  Current pins: phones `H265 + AAC 44.1k/2ch` (phone1 tested; 2-5 assumed same), drones
  `H264 + AAC 48k/2ch` (**guessed rate**, pending a real-drone verify). A dashboard that shows
  expected-vs-actual and flags the mismatch would make this self-service.
- **Reusable building blocks already here:** `api/routes/status_mediamtx.py`,
  `web/src/views/{Mediamtx,Cameras,Radio}.svelte`, `web/src/lib/{phone,drone,whep,live}.ts`
  (WHEP = WebRTC playback → live thumbnails are cheap), and the `routes.ts` `SECTIONS` nav
  pattern for adding a top-level tab.
- **Single-source-of-truth opportunity.** Feed config is currently maintained in **three**
  places that drift: the box `mediamtx.yml`, `paul-network-docs` device page, and the markdown
  quick-ref. A **feed registry** (one file describing each path: host, transport, codec,
  secret, send config, OBS URL) that generates the MediaMTX `paths:`, this dashboard, and the
  markdown quick-ref would kill the drift. Worth considering as the data model for this view.

## Open items for the real plan

- Cloud-hub status reachability (the tunnel/aggregation decision above).
- Registry-as-source-of-truth vs. dashboard-reads-mediamtx.yml.
- Phased scope: MVP = interactive config reference (replaces the markdown); then live status;
  then codec-mismatch alerts + live thumbnails.
- Secrets in the UI (dashboard is trusted-LAN, no auth today — consistent with the low-
  sensitivity stance, but name it).
- Whether the van hub should also accept phone paths (today: cloud-only).
