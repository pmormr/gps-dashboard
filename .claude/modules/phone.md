# Phone Location History

The user's **Google Timeline** history imported as a `phone_*` tier and rendered
as a time-scrubbed, color-by-mode map overlay — 15 years of breadcrumb alongside
van GPS and drone tracks. The **history** half of the phone-tracking theme; the
*live* half (OwnTracks-over-MQTT) stays parked in
`plans/meshtastic-platform-plan.md` — different transport, different tier
lifecycle, deliberately not part of this subsystem.

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
time columns indexed for the windowed reads. First real import 2026-07-02:
22,201 paths / 231,290 thinned pts (from 307,753) / 15,435 visits / 13,608
activities.

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
