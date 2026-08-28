# Phone Tracking (OwnTracks) Plan

> Living plan. **Started 2026-08-28.** Continuous self-hosted logging of the
> user's phone position: OwnTracks (Android, already installed) → OwnTracks
> Recorder container on rex-nas → pulled into a van-side tier over the
> site-to-site WireGuard. The **live** half of the phone-tracking theme; the
> history half (Google Timeline import, `.claude/modules/phone.md`) is landed
> and stays a separate subsystem. Revived from the parked note that lived in
> the tail of `plans/meshtastic-platform-plan.md`.

## Context

Intent is **continuous self-hosted logging** — a go-forward complement to the
occasional manual Timeline export — not live presence, though a live "latest
position" read falls out for free. One phone now; multi-device later is cheap
(OwnTracks user/device identity is in the payload from day one).

Load-bearing existing pieces: rex-edge `wg1` with roaming peers (laptop `.11`,
vps `.12`, van `.13`) and per-peer containment precedent (the vps peer is
firewalled to rex-nas:514); the van↔home site-to-site WG (primary path since
2026-07-26) with proven Pi→rex-nas flows (DB backup push, drone footage); the
`gps-drone-sync` timer-oneshot pattern; the Timeline `phone_*` tier + Phone map
layer. Home-side specifics (rex-nas container, rex-edge peer config) are
documented in the network vault, not here.

## Decisions

- **PT1 — Collector = OwnTracks Recorder on rex-nas; the van pulls.** Not a
  mosquitto bridge into `mqttbus/ingest.py`: the Recorder keeps the van tier
  **fully rebuildable from an external source** (the project's tier principle),
  survives arbitrary off-grid gaps (a pull catches up; a bridge queue overflows
  silently), and is an independent debug view of what the phone actually sent.
- **PT2 — OwnTracks HTTP mode, not MQTT.** Deliberate deviation from the
  parked note: with the Recorder as collector, the van's MQTT bus is no longer
  in the path, so MQTT would only add a home broker as a middleman. HTTP mode
  batches the phone's queued fixes per POST and is the friendlier mode on
  flaky cell links. Recorder runs MQTT-disabled (`OTR_PORT=0`). The van bus
  stays GPS/sensor-only.
- **PT3 — Transport security = WireGuard peer, zero public exposure.** The
  phone joins rex-edge `wg1` as a roaming peer (keypair auth, next free slot).
  **Amended 2026-08-28:** the peer is a full **road-warrior** peer — the same
  split-tunnel AllowedIPs and trust class as the laptop peers, *not* contained
  to rex-nas:8083 as originally sketched — because the phone should reach the
  home and van networks generally when away, not just the Recorder. (Cost: a
  compromised phone key reaches everything a laptop key does — accepted, same
  owner-device posture.) OwnTracks targets the Recorder's LAN address, which
  is valid both from home WiFi and through the tunnel. No TLS or app-layer
  auth inside the tunnel: identity is the OwnTracks user/device fields; trust
  is the network layer — the same trusted-LAN model as the dashboard itself.
  A public HTTPS path (e.g. via the vps reverse-proxy edge) is rejected while
  WG suffices: it adds attack surface and an availability chain for no UX gain
  (the app buffers through outages regardless).
- **PT4 — Own append-only tier, separate from the Timeline tier.**
  `owntracks_points` (user, device, ms-UTC `timestamp`, lat/lon, accuracy,
  altitude, velocity, battery), unique on (device, timestamp), idempotent
  INSERT OR IGNORE sync. No dedup against `phone_track_points` or the van
  track — the same different-provenance stance as phone.md. Timeline imports
  continue unchanged as the occasional full-history refresh.
- **PT5 — Converge with Meshtastic at a "tracked entities" overlay later.**
  First render is a minimal extension of the existing Phone map layer; the
  shared overlay waits until a second live source (mesh) exists.

## Phases

- **P0 — Home side + phone (no repo code).** Recorder container on rex-nas
  (HTTP-only, `/store` volume, LAN-bound); WG peer + containment rule on
  rex-edge; OwnTracks app → HTTP mode, endpoint URL, user/device identity.
  Verify fixes land in the Recorder away from home; document in the vault.
  **Done 2026-08-28** (Recorder verified end-to-end; peer `10.1.250.14/32`
  `phone-pmorgan`; live fixes landing over HTTP). Outcomes: the 13,744-message
  backlog **did not survive** the MQTT→HTTP mode switch (accepted loss — the
  Timeline export covers that period); NAT hairpin from home WiFi **works**
  (the tunnel handshakes from inside the LAN, learned endpoint = the phone's
  LAN address), so WG stays always-on. Still open: verify a fix lands over
  cell away from home.
- **P1 — Van tier.** `owntracks_points` schema in `api/db.py`;
  `tools/sync_owntracks.py` (Recorder REST, windowed `from`/`to`, per-device
  cursor = MAX(timestamp), idempotent, KeyboardInterrupt → 130);
  `gps-owntracks-sync.timer` oneshot at **5 min** (near-live latest marker;
  the pull preflights the Recorder with a short timeout and no-ops off-grid,
  drone-style unconditional re-enable in the Pi's post-receive hook, per
  Deployment). Read API (settled 2026-08-28): `GET /api/phone/owntracks`
  (windowed) + `GET /api/phone/owntracks/latest`, in `api/routes/phone.py` —
  PT4's tier separation is data lifecycle, not route-file organization.
- **P2 — Frontend.** The Layers panel's Phone section grows a live toggle:
  trailing breadcrumb following the global time selection + latest-position
  marker. Single-entity styling until PT5's overlay exists.

## Open

- ~~**Home-WiFi behavior**~~ — resolved 2026-08-28: NAT hairpin works from
  home WiFi (handshake completes from inside the LAN), WG stays always-on.
- **Away-from-home verification:** confirm a fix lands over cell + the tunnel
  on the next outing (config is IP-based, valid on both paths).
- **Recorder `/store` retention + backup scope on rex-nas** — it is now the
  rebuild source of truth for this tier; fold into the NAS backup story
  (vault matter).
- **Reporting-mode tuning** (significant-changes vs. move mode, intervals)
  after a week of real data.
- **Multi-device onboarding** (other phones): per-device WG peer + OwnTracks
  identity; schema and sync already handle it.
