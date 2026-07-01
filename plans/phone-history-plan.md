# Phone Location History Import

Batch import of the user's **Google Timeline** location history into a new
`phone_*` tier of `gps_history.db`, rendered as a map overlay alongside van GPS
and drone tracks. The **history** half of the phone-tracking theme (the *live*
half — OwnTracks-over-MQTT — stays parked in `plans/meshtastic-platform-plan.md`
and is deliberately **not** part of this work: different transport, different
tier lifecycle).

Architecturally this is the **drone importer pattern** (`tools/import_drone.py`
→ per-source, fully-rebuildable-from-source tier): parse → thin with the shared
`processor/simplify.py` Reumann–Witkam → load a derived tier the frontend
overlays. No daemon, no live stream.

## Source — the on-device Timeline export

`~/My Drive/Timeline.json` (87 MB, synced from Google Drive to this laptop). The
**current on-device schema** (phone → Settings → Location → Timeline → Export),
*not* legacy Takeout `Records.json`. Three top-level keys; profiled 2026-07-01:

| Path | Count | Span | Use |
|---|---|---|---|
| `semanticSegments[].timelinePath` | **307,753 pts** / 22,201 segs | 2011–2026 (full) | The breadcrumb (map trail). Coords are strings `"40.79°, -77.86°"`; each point has its own `time` |
| `semanticSegments[].visit` | 15,435 | 2011–2026 | Place visits — `topCandidate.placeLocation.latLng`, `placeId`, `semanticType`, `probability` |
| `semanticSegments[].activity` | 13,608 | 2011–2026 | Trip segments — `start`/`end` latLng, `distanceMeters`, `topCandidate.type` (10.5k `IN_PASSENGER_VEHICLE`, walking, cycling, 32 flying, trains, ferries) |
| `rawSignals[].position` | 15,350 | **last 30 days only** | **DROPPED** — not historical; the one month it covers is already covered far better by the van's own 5 Hz logger |

Timestamps are ISO-8601 with offset (`2011-04-16T10:08:00.000-04:00`) →
canonical ms-UTC via `api.db.canonical_timestamp`, same axis as `gps_points`.

## Locked decisions

- **D1 — semantic layer stays in its own tables, NOT `annotations`.** `annotations`
  is the user's *curated* trips/bookmarks; auto-dumping 29k Google segments would
  bury them. Visits/activities render as a *layer*; the user can promote any one
  to a real annotation by hand.
- **D2 — full-replace, not incremental.** Google exports are cumulative (full
  history each time), so each run truncates the `phone_*` tables and reloads.
  Fully rebuildable from the export; no dedup key needed.
- **D3 — drop `rawSignals`** (see table).
- **D4 — transport: scp `Timeline.json` to the Pi, run the importer there against
  the real DB** (`/mnt/nvme/data/gps_history.db`). Occasional manual re-export; no
  `--api` LAN ingest path (unlike drone). Dev/test runs use `--db ./local.db`.
- **D5 — breadcrumb grouped into `phone_paths` (one row per `timelinePath`
  segment).** Thinning is per-segment (never across time gaps), and per-segment
  rows give the frontend polyline boundaries for free — the drone_flights ↔
  drone_track_points shape, minus the media identity.

## Schema (added to `api/db.py` `init_db`)

- `phone_paths(id, start_time, end_time, n_points, min_lat, min_lon, max_lat, max_lon, imported_at)`
  — one contiguous breadcrumb segment.
- `phone_track_points(id, path_id, timestamp, lat, lon, importance)` — thinned
  breadcrumb; `importance` = RW deviation (m).
- `phone_visits(id, start_time, end_time, lat, lon, place_id, semantic_type, probability)`.
- `phone_activities(id, start_time, end_time, start_lat, start_lon, end_lat, end_lon, distance_m, activity_type, probability)`.

Indexes on the time columns (`phone_track_points.timestamp`,
`phone_paths.start_time`, visits/activities `start_time`) for the windowed reads.

## Plan

### Phase 1 — schema + importer  ✅ done (2026-07-01)
- [x] Add the four tables to `init_db`.
- [x] `tools/import_phone_timeline.py`: parse Timeline.json → per-segment RW thin
      (shared `simplify`) → full-replace load. Pure `parse_*` helpers (coord/time
      string parsing) split from I/O for tests. `--db`, `--dry-run`, `--limit`;
      Ctrl+C → `"\nInterrupted."` exit 130.
- [x] Tests: coord/time parsing, per-segment thinning, a small JSON fixture end-to-end.
- Validated against the real 87 MB export: 22,201 paths (231,290 pts thinned from
  307,753 at ε=20 m), 15,435 visits, 13,608 activities; re-run confirms full-replace.

### Phase 2 — API read  ✅ done (2026-07-01)
- [x] `GET /api/phone/tracks?start=&end=&bbox=&limit=` — `phone_paths` overlapping
      the window, thinned points embedded. Endpoints (`importance=0`) always kept,
      remaining budget filled by top-`importance` interior vertices; `truncated`
      reports interior loss. `api/routes/phone.py`, registered in `app.py`.
- [x] `GET /api/phone/places?start=&end=&bbox=&limit=` — visits + activities
      overlapping the window (visits bbox on the point, activities on start-or-end).
- [x] Flask-client tests against a temp DB (10, `tests/test_phone_api.py`).

### Phase 3 — frontend layer  ⟵ current
- [ ] "Phone history" toggle in the Layers panel (drone-style overlay): breadcrumb
      polylines + visit pins, filtered by the global time selection.
- [ ] Vitest where it earns it. Build + commit `static/dist/`.

### Phase 4 — run for real
- [ ] scp `Timeline.json` to the Pi; run the importer against the live DB; verify
      counts; `git push all` (dist committed).
- [ ] Fold the durable bits into a new `.claude/modules/phone.md`; drop this plan.

## Open / deferred
- **Colour-by / activity-type styling** of the breadcrumb (driving vs walking vs
  flying) — defer to Phase 3 polish.
- **Van-track de-duplication** — recent Google history overlaps the van's own GPS.
  Not de-duped (different provenance, both interesting); revisit only if the map
  gets noisy.
- **`place_id` resolution** to human place names needs Google's Places API
  (online, per-lookup) — out of scope; store the id, render coords.
