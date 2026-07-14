# Trip Planner Plan

**Status: SHAPED 2026-07-09 — core decisions resolved in session; review the
flagged calls (decisions 8–10) before Phase 1.** Treat this doc as the durable,
living plan — check items off as they land, record decisions inline.

## Context

A planning layer for future travel: mark "want to go" places (from the
attractions tier or free-dropped pins), accumulate them into a pool, then
assemble pools into ordered **trips** on a new Planner tab — geospatially, on
the shared map engine. Road routing is explicitly out of scope for v1
(great-circle legs + haversine distances), but the data model is the upstream
half of `plans/navigation-plan.md`: an ordered stop list is exactly what a
future multi-leg `POST /api/route` consumes.

Symmetry with the existing model: **annotations are curated time** (where was
I), **saved places are curated space** (where do I want to be). Same
pure-metadata philosophy — no FKs into synced tiers.

Relationship to the other active plans:

- **The places tier** (`.claude/modules/places.md` — landed) — places live in
  a rebuildable, un-backed-up sidecar (`places.db`, ~11.6M rows). Saved
  places are user data and live in `gps_history.db` (the backed-up DB),
  **snapshotting** name/lat/lon at save time with `(source, source_id)` as a
  soft re-link only. The soft link is DB-agnostic and survives the tier's
  full-replace re-imports. (The broad "places substrate" question is that
  tier's turf — this table stays narrowly the want-to-go pool.)
- **`plans/navigation-plan.md`** — locked. The junction (navigate-to-next-stop
  from a trip) is deferred to that plan's frontend phases; nothing here
  anticipates it beyond stop ordering.

---

## Confirmed decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | UI home | **New Planner tab** (9th destination) | User call. Phone tab-bar crowding accepted for now (scroll); a later pass addresses nav density. Not folded into Attractions — marking happens there, planning happens here. |
| 2 | Pool vs trips | **Separate concepts**: a global saved-places pool; trips draw from it | Matches the workflow: mark over time, assemble later. A place can be in 0..n trips. |
| 3 | Attraction link | **Snapshot + soft link** — copy name/lat/lon at save time; keep `(source, source_id)` for detail re-link; never an FK on `attractions.id` | Imports are full-replace per source → numeric ids are unstable. A saved place survives the attraction vanishing from a re-import (renders from its snapshot; the detail link degrades gracefully). |
| 4 | Free pins | **In v1** — long-press/click on the planner map drops a named pin (`source` NULL) | UI-cost-only (the table is source-nullable regardless); covers everything the POI expansion hasn't landed (friends' places, dispersed campsites). |
| 5 | Timing model | **Relative-first, two layers**: nullable `day` (integer) + `slot` (morning/afternoon/evening/overnight) per stop; optional trip `start_date` concretizes days; nullable per-stop **hard anchor** (local date + time window) for tickets/reservations | User call. Plans stay valid before departure is known; shifting departure edits one field. Anchors capture "must be at X at time T"; with `start_date` set, anchor-vs-day conflicts become checkable. |
| 6 | Anchor time storage | **Park-local date/time strings** (`YYYY-MM-DD`, `HH:MM`), same convention as `attraction_event_dates` — not ms-UTC | Future local times + timezone math is a trap; store and display as written. Only *actual* track data uses `canonical_timestamp`. |
| 7 | Main-map presence | **Want-to-go pins as a DataLayers toggle** on the Map view | "Where are my marked places relative to me" is half the value. Trip *lines* on the main map are deferred. |
| 8 | Table naming | **`saved_places`** (not `places`) | ⚑ flagged call. Avoids collision with the NPS `places` API asset (importer coordinate join) and Overture "Places" language in the POI plan. **2026-07-09: the attractions tier itself was renamed to `places` (POI plan decision 10), so this choice is now load-bearing — and the API surface below must move off `/api/places` (now the tier's read routes) to `/api/saved-places`. Route paths in this doc are stale pending that edit.** |
| 9 | Place delete semantics | **`DELETE /api/places/:id` → 409 listing referencing trips; `?force=1` cascades the trip_stops** | ⚑ flagged call. Restrict-by-default keeps a trip from silently losing a stop; force is the UI's "remove from N trips and delete" confirmation path. |
| 10 | Stop reorder API | **`PUT /api/trips/:id/stops` replaces the whole ordered list, transactionally** | ⚑ flagged call. Drag-reorder maps to one idempotent write; no seq-patching races or gap bookkeeping. Per-stop field edits (day/slot/anchor/notes) get a separate `PATCH`. |

---

## Open decisions (execution-time — resolve in the named phase)

| # | Decision | Notes |
|---|----------|-------|
| A | Saved-state read shape for the ⭐ button | `AttractionDetail` needs "is this attraction already saved?" — probably `GET /api/places?source=&source_id=` on detail mount. Verify it stays one cheap indexed read (Phase 2). |
| B | Want-to-go pins vs the POI rank gate | Once the POI plan's rank×zoom gating lands, saved pins must **bypass** it (always visible when the layer is on). Trivial if the overlays stay separate layers — just don't merge them (Phase 2/3). |
| C | Trip `status` vocabulary | `planning`/`archived` minimum; whether `active` earns a slot (and any Drive-view meaning) decided when the nav junction lands (Phase 4 or later). |
| D | Conflict-flag rules | With `start_date` set: anchored stop whose date ≠ `start_date + (day-1)` flags; anchors out of seq order flag. Exact rules + rendering in Phase 4. |

---

## Data model (all in `gps_history.db` — user-curated, rides the backup)

- `saved_places(id, name, lat, lon, notes, source, source_id, created_at)` —
  the pool. `source`/`source_id` NULL = free pin; non-NULL = snapshot of an
  attraction (`nps`/`ridb`, later `osm`/`overture`). `created_at` ms-UTC.
  Index `(source, source_id)`.
- `trips(id, name, notes, status, start_date, created_at)` — `start_date`
  nullable local `YYYY-MM-DD`.
- `trip_stops(id, trip_id, place_id, seq, day, slot, anchor_date,
  anchor_time_start, anchor_time_end, notes)` — ordered membership. `day`
  nullable int (1-based); `slot` nullable
  `morning|afternoon|evening|overnight`; anchor fields nullable local strings
  (decision 6). Index `(trip_id, seq)`.

## API surface (all new routes in `api/routes/planner.py`)

- `GET/POST /api/places` · `PATCH/DELETE /api/places/:id` — pool CRUD; GET
  filters by `source`+`source_id` (the saved-state check) and `bbox`.
- `GET/POST /api/trips` · `GET/PATCH/DELETE /api/trips/:id` — trip CRUD; GET
  `:id` returns stops joined with their places, ordered.
- `PUT /api/trips/:id/stops` — full ordered replace (decision 10);
  `PATCH /api/trips/:id/stops/:stop_id` — day/slot/anchor/notes edits.
- Attractions natural-key lookup (`GET /api/attractions?source=&source_id=`
  or equivalent) — the detail re-link from a saved place; lands here since
  this plan needs it first.

---

## Traps (identified up front)

1. **`attractions.id` is not stable** — full-replace per source reassigns ids,
   and the POI plan multiplies re-import frequency and scale. Snapshot +
   natural-key soft link only (decision 3). The UI must render a saved place
   entirely from its snapshot when the re-link misses.
2. **User tables never migrate to the sidecar.** When the POI plan's Phase 0
   moves the attractions tier to `attractions.db`, `saved_places`/`trips`/
   `trip_stops` stay in `gps_history.db` — the sidecar is explicitly outside
   the backup path.
3. **The map engine is a keep-alive singleton** with a handoff contract
   (Drive is the precedent): Planner on leave must clear its own overlays and
   unsubscribe gestures; Map's track effect refits on remount. Data layers
   the user set are left alone.
4. **No ms-UTC for future local times** (decision 6). The one place trip data
   meets the canonical time axis is `created_at` — everything schedule-shaped
   is local strings, displayed as written.
5. **`AttractionDetail` is shared** (Attractions view + map sheet) — the ⭐
   button lands in both automatically; its saved-state fetch must be
   mount-scoped, not reactive-spam (open A).
6. **Reorder atomicity** — the whole-list PUT wraps in one transaction; a
   partial seq write must be impossible (decision 10).
7. **Planner-map pin collisions** — want-to-go pins render as their own layer
   above attractions pins, visually distinct; never merged into the
   attractions overlay (open B).

---

## Constraints carried from the project

- **Offline-first.** Everything is local SQLite reads/writes; no WAN anywhere.
- **Committed SPA build** — rebuild + commit `static/dist/` before push; the
  Pi never builds.
- **Tests ride the pattern**: route CRUD via the Flask-client + temp-DB
  fixture; any pure geometry/ordering logic (leg distances, conflict rules)
  unit-tested (pytest server-side, Vitest client-side).
- **GPS logging is sacred** — nothing here touches the logger or processor.

---

## Phases

Each phase independently shippable.

### Phase 1 — Data model + API

- [ ] Tables in `api/db.py` `init_db` (schema above)
- [ ] `api/routes/planner.py`: pool + trip CRUD, whole-list stop PUT, stop PATCH (decisions 9–10)
- [ ] Attractions natural-key lookup read
- [ ] Route tests (Flask client + temp DB): CRUD, 409/force delete, transactional reorder, source-filter read

### Phase 2 — Marking

- [ ] ⭐ "Want to go" toggle on `AttractionDetail` (snapshot write; saved-state read, open A) — appears in Attractions view + map sheet for free
- [ ] Map view: DataLayers toggle + saved-places pin overlay (own layer, open B) + on-map legend chip

### Phase 3 — Planner tab

- [ ] Route/shell entries (`/planner`, 9th tab) + `stores/planner.svelte.ts` (module singleton — session survives tab switches, like attractions)
- [ ] Planner view on the shared engine (handoff contract, trap 3): pool pins, active-trip selection, tap-to-append
- [ ] Stop list panel (desktop side pane / mobile bottom sheet): drag-reorder → PUT, remove, per-stop notes
- [ ] Trip polyline (great-circle) + per-leg/total haversine distances (labeled as straight-line estimates)
- [ ] Free-pin drop: long-press/click → name → POST (decision 4)

### Phase 4 — Timing

- [ ] Day/slot editing on stops; day-grouped list rendering
- [ ] Trip `start_date`; concretized day dates in the list
- [ ] Hard anchors (date + time window) with distinct pin/list treatment
- [ ] Conflict flags (open D)

### Deferred pile (flagged, not scoped)

- **Navigate-to-next-stop** — junction with `plans/navigation-plan.md` Phase 3
  (trip stop → Drive destination store → route).
- **Trip lines on the main Map** as part of the data layer (decision 7 note).
- **"Suggest order"** — nearest-neighbor pass over a trip's stops; cheap,
  offline, build only if manual ordering proves tedious.
- **Event → anchored stop** — "add to trip" from `EventDetail`, prefilling the
  anchor from the event's park-local date/time (natural fit for the ticket
  case).
- **Trip ↔ history interplay** ("have I already been near this?") — parked;
  something there, shape unknown (user call 2026-07-09).
