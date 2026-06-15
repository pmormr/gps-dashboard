# Annotations & Time Picker UI Plan

## Context

The dashboard today has two tabs — **Timeline** (date picker → day's points) and
**Trips** (labeled time ranges with their own list + map). The split made sense
when "a trip" was a special concept, but reviewing the data in practice it's
clear that:

- A trip is just a labeled time range, and there's no reason ranges and
  point-in-time bookmarks ("fuel up at Moab", "saw bear") shouldn't live in the
  same list, on the same map, queryable the same way.
- The date picker is too narrow — a single day at a time fights the way GPS
  history is naturally reviewed (the last 24h, a 7-day window around a
  remembered event, an explicit absolute range).
- Two map instances + two parallel browsing surfaces is duplicate scaffolding.

So: one map-centric view, a Graylog-style time picker, and a single
`annotations` concept that covers both points and ranges.

---

## Confirmed decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Data model | One `annotations` table; `end_time` nullable (NULL = point bookmark) | No new table, no migration risk; same query shape. Rename `trips` → `annotations` so the code stops lying. |
| 2 | Trip nomenclature | Dropped entirely. "Annotation" everywhere — code, API, UI | Two words for the same idea will keep confusing us. |
| 3 | Time picker model | `{ anchor, mode, window, live }` — anchor is the query datetime, mode is `last` / `around` / `range`, window is a duration, live pins anchor to `now()` | One state shape covers Graylog-style relative ranges, centered windows, and absolute ranges. Live is just a flag on the anchor. |
| 4 | Live only in `last` mode | `around` with anchor=now would show half the window in the future — meaningless. `range` is two anchors and doesn't fit | Hides/disables Live outside `last`. |
| 5 | Re-centering policy | Map only re-centers when (a) Live is on, or (b) an annotation is clicked. Otherwise the user can pan/zoom freely without the view fighting them | Browsing ≠ navigation. Navigation mode is opt-in. |
| 6 | Downsampling strategy | Tiered: full detail up to ~24h, pick-one-per-bucket beyond. Start simple — no Visvalingam/Douglas-Peucker — and revisit if the trail looks wrong | "Zoom for detail" already covers the gap. Premature optimization otherwise. |
| 7 | Viewport filter | `?bbox=W,S,E,N` on `/api/points` excludes off-screen rows server-side | Useful once the user zooms into part of a long-range query. |
| 8 | Default view | Live, last 24h, map centered on most recent fix | What you almost always want when you open the page. |

---

## Constraints carried from the project

- **Offline-first.** No new runtime deps that need a CDN. Any new JS gets
  vendored under `static/vendor/`.
- **Mobile-first.** Primary client is a phone over van WiFi. Picker, drawer,
  and floating controls must all work touch-first.
- **GPS logger is untouched.** This is a viewer/UI effort. Logger, schema for
  `gps_points`, and the ingest path are out of scope.
- **One SQLite DB.** Same `gps_history.db` — no separate store.

---

## Phase 1 — Data model & API

The schema-and-endpoint groundwork. Cheap to do first, unblocks everything else.

- [x] **1.1** Rename `trips` → `annotations` and make `end_time` nullable.
  Idempotent in-place table swap (SQLite has no ALTER COLUMN to drop NOT
  NULL); migration runs at the top of `init_db` before the CREATE IF NOT
  EXISTS. Existing rows preserved.
- [x] **1.2** Routes renamed (`routes/trips.py` → `routes/annotations.py`,
  blueprint `trips_bp` → `annotations_bp`, `/api/trips*` → `/api/annotations*`).
  Frontend `trips.js` → `annotations.js`, modules `Trips`/`TripsMap` →
  `Annotations`/`AnnotationsMap`. HTML ids, CSS selectors, tab label, and
  overlay copy follow. CLAUDE.md updated.
- [x] **1.3** Tiered downsampling on `/api/points`: `?bucket=Ns` does
  `GROUP BY CAST(strftime('%s', timestamp) AS INTEGER) / N`, returning one
  representative row per bucket. Combines cleanly with `bbox`.
- [x] **1.4** Viewport filter on `/api/points`: `?bbox=W,S,E,N` adds
  `WHERE lat BETWEEN S AND N AND lon BETWEEN W AND E`. 4 floats validated.
- [x] **1.5** Annotation endpoints accept/return nullable `end_time`. List
  returns all annotations (no date filter); `point_count` is NULL for points,
  integer for ranges. PATCH supports point↔range transitions.

## Phase 2 — Time picker

The new query surface. Replaces the date input and Live button on the Timeline.

- [x] **2.1** `TimePicker` in `static/js/timepicker.js`. Trigger button +
  popover (desktop) / bottom sheet (mobile). Mode tabs Last / Around /
  From→To; anchor field with [Now] button; window number + unit dropdown;
  Live checkbox (gated to Last); preset chips short-circuit to
  `{mode:'last', window:preset, live:true}` and apply immediately.
- [x] **2.2** Picker drives `Timeline` via `TimePicker.onChange(loadRange)`.
  Live polling owned by the picker (30s emit). Old `loadDate(dateStr)` + Live
  button gone. Slider rebuilds with each range and steps at ~1k ticks across
  the span. Live re-emits skip `fitBounds` so the map doesn't jerk.
- [x] **2.3** `bucketFor(spanMs)` in `timeline.js`: ≤24h none, ≤7d 30s,
  ≤30d 300s, longer 1800s. Sent via `API.getPoints(..., {bucket})`. Bbox
  filter is wired through `api.js` but not yet driven by map state —
  premature without a pan/zoom re-fetch loop, deferred.
- [x] **2.4** Removed `<input type="date">` and `#tl-live-btn`. The slider
  is now strictly a sub-range zoom inside the loaded window. The status
  text shows `<count> pts (truncated) · <N>s buckets`.

## Phase 3 — Unify map + annotations

Collapse the two tabs into one map-centric view.

- [ ] **3.1** Remove the Trips tab and the second map instance. Keep one
  Leaflet+MapLibre map. Tab bar shrinks to status links only (or to nothing,
  TBD when we get there).
- [ ] **3.2** Annotation list as a collapsible side drawer (right edge on
  desktop, bottom sheet on mobile). Replaces today's `trips-list-pane`.
- [ ] **3.3** Render annotations on the map: pins for points, highlighted
  track segments for ranges (using the points already loaded for the current
  window when the range overlaps).
- [ ] **3.4** Render annotations on the slider: shaded bands for ranges,
  ticks for points. Lets the user see what's tagged inside the loaded window.
- [ ] **3.5** Click an annotation:
  - Range → picker switches to `range` mode with its `(start, end)`.
  - Point → picker switches to `around` with current window, anchor = its
    timestamp. Live turns off.
  - Map pans to the nearest logged GPS fix to the annotation's timestamp.
- [ ] **3.6** "Add Annotation" button captures slider head (or `now` in Live)
  as the timestamp. Form: name, notes, optional end-time. Empty end =
  point; populated end = range. Reuses existing overlay.

## Phase 4 — Reclaim map real estate (#1)

Originally the highest-impact quick win; sequencing it last so the controls
above are settled before deciding what collapses where.

- [ ] **4.1** Picker trigger + Add-Annotation + Live status collapse into a
  floating overlay over the map (similar to the existing `⚙ Labels` panel).
  Auto-hide on idle, tap/click to expand.
- [ ] **4.2** Slider becomes an overlay strip along the bottom of the map.
  Always visible when there's loaded data; thin and unobtrusive.

---

## Out of scope (deliberately deferred)

- Cross-linking deep into other views (originally #4 in the larger UI brainstorm
  beyond annotations themselves) — covered organically by the unified view.
- Richer trail rendering: color-by-speed, direction chevrons, head dot
  (originally #2). Worth doing, but separate plan.
- Per-trip elevation profiles and speed-over-time charts (originally #3).
  Separate plan once the unified view is settled — uPlot is already vendored
  from `/sensors`.
- Better decimation (Visvalingam etc.). Revisit if pick-one-per-bucket looks
  bad in practice.
- Annotation list pagination. Hundreds of annotations is fine without it.
