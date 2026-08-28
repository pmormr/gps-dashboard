# Phone Tracking

Two tiers, one theme, deliberately separate tables: the **history** half — the
user's **Google Timeline** export imported as the `phone_*` tier, a
time-scrubbed color-by-mode map overlay (15 years of breadcrumb alongside van
GPS and drone tracks) — and the **live** half — continuous self-hosted OwnTracks
logging into `owntracks_points` (last section). Different sources, different
lifecycles (full-replace vs. append-only); only the HTTP surface is shared
(`api/routes/phone.py` serves both).

Architecturally this is the **drone importer pattern** (batch `tools/` importer →
per-source, fully-rebuildable derived tier → frontend overlay), minus the LAN
ingest route: no daemon, no live stream, no `--api` path.

## Source — the on-device Timeline export

`Timeline.json` from the phone itself (Settings → Location → Timeline → Export),
synced to the laptop at `~/My Drive/Timeline.json` (~90 MB). This is the
**current on-device schema**, *not* legacy Takeout `Records.json`. What's used:

- `semanticSegments[].timelinePath` — the breadcrumb (~308k pts / 22k segments,
  2011–2026). Coords are strings (`"40.79°, -77.86°"`); each point has its own
  ISO-8601-with-offset `time` → canonical ms-UTC, same axis as `gps_points`.
- `semanticSegments[].visit` — place visits (`topCandidate.placeLocation.latLng`,
  `placeId`, `semanticType`, `probability`).
- `semanticSegments[].activity` — trip segments (start/end latLng,
  `distanceMeters`, `topCandidate.type`: driving dominant, plus walking/cycling/
  transit/flying).
- `rawSignals` — **dropped**: only the phone's trailing ~30-day raw buffer, and
  that month is already covered far better by the van's own 5 Hz logger.

## Importer — `tools/import_phone_timeline.py`

Parse → per-segment Reumann–Witkam thin (shared `processor/simplify.py`,
ε = 20 m default) → **full-replace** load. `--db`, `--epsilon`, `--limit`,
`--dry-run`; Ctrl+C → `"\nInterrupted."` exit 130. Pure `parse_*` helpers
(coord/time strings) are split from I/O for tests.

At import, each track point is tagged with the `activity_type` of the covering
`activity` interval (`bisect` interval join; ~60% of points covered) — that
column is what the frontend colors by.

**Transport is scp-then-run:** copy `Timeline.json` to the Pi, run the
importer there against the live DB. Occasional manual re-export; the export
lives at `~/Timeline.json` on the Pi after the last run.

```bash
scp ~/My\ Drive/Timeline.json pmorgan@192.168.42.178:~
ssh pmorgan@192.168.42.178 "cd /mnt/nvme/gps-dashboard && \
  GPS_DB_PATH=/mnt/nvme/data/gps_history.db \
  ~/.local/bin/uv run tools/import_phone_timeline.py ~/Timeline.json"
```

(Non-interactive ssh doesn't have `~/.local/bin` on PATH — spell out the uv path.)

## Data tier

Derived, fully rebuildable from the export. Schema in `api/db.py` `init_db`;
time columns indexed for the windowed reads. (Import-size snapshots live in the
phone-history memory, not here.)

- `phone_paths` — one row per contiguous `timelinePath` segment: thinning
  never crosses a time gap, and per-segment rows give the frontend polyline
  boundaries for free (the `drone_flights` ↔ `drone_track_points` shape, minus
  media identity).
- `phone_track_points` — the thinned breadcrumb; `importance` = RW deviation (m),
  `0` marks segment endpoints (the skeleton); `activity_type` from the interval
  join.
- `phone_visits` / `phone_activities` — the semantic layer.

## API — `api/routes/phone.py`

Read-only; all filters (`start`/`end`/`bbox`) optional, **overlap** not
containment.

- `GET /api/phone/tracks?start=&end=&bbox=&limit=` — overlapping `phone_paths`
  with thinned points embedded. Size-guarded like `/api/points`: endpoints
  (`importance = 0`) always kept, the remaining budget filled by top-`importance`
  interior vertices (ranked globally, regrouped per path in time order);
  `truncated` ⇒ interior loss only.
- `GET /api/phone/places?start=&end=&bbox=&limit=` — visits + activities
  overlapping the window (visits bbox on the point; activities on start-or-end).

## Render — `web/src/lib/phone.ts` + map layers

The **Phone history** toggle + mode legend live in the Layers panel. Unlike the
drone overlay (fetch-all-once), the breadcrumb **follows the global time
selection** — 15 years all at once is a hairball — so `Map.svelte` refetches on
selection change via an `$effect`, only while the toggle is on.

`phone.ts` is pure (no MapLibre import, so the Layers chrome can import the
legend without pulling the map engine): mode-group palette
(driving/walking/cycling/transit/flying/unknown), **run-splitting** (MapLibre
colors per-feature, so color-by-mode = one LineString per contiguous same-mode
run, adjacent runs sharing the boundary vertex), visit-pin FC with prebuilt
popup HTML, and the `syncPhone`/`clearPhone` controller (monotonic token drops
stale fetches while scrubbing). `map.ts` stays domain-free: a `phone-track` line
layer painting `['get','color']`, a `phone-visit` circle layer + popup, and a
`setPhoneData(tracksFC, visitsFC)` façade method.

## Decisions / traps

- **Semantic layer stays in its own tables, NOT `annotations`.**
  `annotations` is user-curated; auto-dumping 29k Google segments would bury
  them. Visits/activities are a *layer*; promote one to a real annotation by
  hand if wanted.
- **Full-replace, not incremental.** Google exports are cumulative (each
  export = the whole history), so every run truncates and reloads. No dedup key.
- **Coords/times are strings** — degree-suffixed coord pairs, ISO-8601 with
  offset; both normalized on the way in (`api.db.canonical_timestamp`).
- **Van-track overlap is not de-duped** — recent phone history overlaps the
  van's own GPS; different provenance, both interesting. Revisit only if noisy.
- **`place_id` stays unresolved** — human place names need Google's Places API
  (online, per-lookup); store the id, render coords/semantic type.

## Live tier — OwnTracks (continuous self-hosted logging)

The go-forward complement to the occasional Timeline export (landed 2026-08-28,
ex-`plans/phone-tracking-plan.md`): the phone's OwnTracks app POSTs each fix to
an **OwnTracks Recorder** container on rex-nas (`:8083`, HTTP-only, MQTT
disabled), and the Pi pulls the Recorder's REST API 5-minutely
(`gps-owntracks-sync.timer` → `tools/sync_owntracks.py`) into the append-only
`owntracks_points` table (unique `(device, timestamp)`; velocity km/h and
battery % as OwnTracks reports them). A near-live latest-position read falls
out of the cadence. Home-side detail (Recorder container, WG peer) lives in the
network vault, not here.

Durable decisions:

- **Recorder-as-collector; the van pulls.** Not an MQTT bridge into the van
  bus: the Recorder's `/store` on rex-nas is the tier's rebuild source of truth
  (delete every row and re-run to backfill), a pull catches up over arbitrary
  off-grid gaps where a bridge queue overflows silently, and the van bus stays
  GPS/sensor-only.
- **OwnTracks HTTP mode, not MQTT** — with the Recorder collecting, a home
  broker would only be a middleman; HTTP batches queued fixes and is the
  friendlier mode on flaky cell links.
- **Transport = WireGuard, zero public exposure.** The phone is a road-warrior
  `wg1` peer on rex-edge (full trust, laptop class — it doubles as the user's
  general remote access to home/van). OwnTracks targets the Recorder's LAN
  address, valid from home WiFi and through the tunnel; NAT hairpin from home
  WiFi works, so WG stays always-on. No TLS or app-layer auth inside the
  tunnel — the dashboard's own trusted-LAN model.
- **No dedup** against `phone_*` or the van track — the same
  different-provenance stance as the history tier.
- **Single-entity styling** until a second live source (Meshtastic) exists;
  the two then converge at a shared "tracked entities" overlay
  (`plans/meshtastic-platform-plan.md`).

Sync (`tools/sync_owntracks.py`): short-timeout preflight → exit 0 when
unreachable (boondocking is normal — the timer just fires again); per
(user, device) cursor = `MAX(timestamp)`, re-pulled minus 1 h slack with
`INSERT OR IGNORE` making the overlap free; users/devices discovered from the
Recorder, so a second phone needs no code. The timer's enable line in the Pi's
post-receive hook is drone-style (unconditional).

API: `GET /api/phone/owntracks` (window/bbox/device filters, oldest first,
capped + `truncated`) · `GET /api/phone/owntracks/latest` (most recent fix per
device, window-independent — the live-marker read).

Render: the Layers panel's **Phone live** toggle — dashed fuchsia per-device
breadcrumb following the global time window, plus a ringed always-current
latest-fix marker (popup: battery/accuracy/altitude) refreshed every 60 s
while on.

Traps / open:

- **The app's outgoing queue does not survive a connection-mode switch** — a
  13,744-message MQTT-mode backlog was lost in the MQTT→HTTP switch. Never
  switch modes expecting a flush, and never point a queued app at a live
  broker with nothing recording — the queue drains into nothing.
- Recorder `/store` backup scope on rex-nas is still open (vault matter).
- Verify a fix lands over cell + tunnel on the next outing; tune the app's
  reporting mode (significant-changes vs. move) after a week of real data.
