# Map-View Redesign Plan

## Context

The map view at `/` grew one data source at a time, and the "annotations" concept —
born as a rename of `trips`, a flat `(id, name, start_time, end_time, notes)` row over
a time window — is now carrying jobs it was never shaped for. The app has outgrown a
single flat list: GPS trail, processor-derived stops/dwells, drone flights, and two
sensor streams (cabin BME680, van OBD) all want a home in the view, and the current
model can't represent them coherently. The result is "clunky."

The diagnosis: **one list is doing three orthogonal jobs at once.** A single annotation
is simultaneously a *label* ("campsite, night 3"), a *navigation target* (click → the
picker jumps and the map pans), and an *analysis container* (the fuel-economy line
bolted onto ranges in `loadEconomies` — a range used as "compute stats for this
window"). Meanwhile the data-on-map axis has grown ad-hoc (drone panel here, sensors on
a wholly separate `/sensors` page), and the time-selection axis is entangled with both.

**The fix is to unbundle, not to enrich.** Stop making annotations richer; give each
job its own home. Three axes:

1. **Selection** — what time/space am I looking at? (navigation, the window)
2. **Layers** — what data is drawn? (trail, stops, drone, sensor overlays)
3. **Marks** — what have I deliberately labeled and want to recall? (the annotation
   proper, slimmed to its real job)

Treat this doc as the durable, living plan — check items off as they land, record
decisions inline. Landed axes fold into `.claude/modules/frontend.md` (and the API/data
sections of `CLAUDE.md`) and drop out of here.

---

## Confirmed decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Model | **Unbundle into three axes** (Selection / Layers / Marks) | The clunk is overload, not missing fields. Each axis has a different shape and lifecycle; cramming them into one list is the root cause. |
| 2 | Approach | **Evolve in place** | It's a working app on a van with no easy rollback. Restructure piece by piece; the view stays usable throughout. No ground-up rewrite. |
| 3 | First axis | **Selection** | It has a concrete, reproducible bug (below), and "the axis is generic time" is foundational — the Layers axis renders sensor density onto that same axis. |

---

## Status — resume here (paused 2026-06-23)

**Done (Selection axis):** S1 axis = window · S2 stop-overlap select · S3 stop blocks +
constant map dots · S4 legible density track (canvas coverage lane above the slider) ·
S6 picker docked above the slider (popover opens upward) · S7 Zoom to Range. The
slider-extent and distinct-dwell-marker carry-ins are folded in. **Only S5 (consolidate
the strip into one canvas, retiring noUiSlider) is left on this axis — flagged "evaluate."**

**Done (Marks axis, landed early):** 📍 Bookmark Here (one-tap, Live-only, latest fix) ·
✎ edit/rename UI in the drawer (reuses the modal, `PATCH`).

**Done (cleanup):** docs reconciled (`frontend.md` + `CLAUDE.md`) · dead point-form
branch pruned · merged feature branches deleted.

**Next session picks up — by axis:**
- **Selection:** S5 (consolidate noUiSlider into a custom canvas strip, *evaluate* — the
  density canvas from S4 is the seed it grows into); S2 sub-item (bracketing narrow
  moving windows, deferred to whenever the strip gets richer).
- **Layers** (not started): layers panel, trail color-by, sensor/OBD/drone onto the map,
  retire the divorced `/sensors` page. **Sensor density renders onto the S4 lane.**
- **Marks** (continue the rework): mark *types*; analysis → an **"inspect this window"**
  panel that retires `loadEconomies` (loose end #4); bounds-editing (point↔range);
  stops→marks (denoise Phase 6, [[denoise-progress]]).

**Open decisions (need the user):**
- **Loose end #3 — Marks panel overlap.** Mark Start / Mark End / Use Marks now overlaps
  brush + Zoom to Range + Create Range. Its one distinct value is *persisting* range
  boundaries across reloads (the `marks` table). Decide: retire it, or keep as the
  persisted-range tool and de-emphasize.
- Surface dwell length on the map dot too? (user leaned **leave out** — timeline block
  already carries it).

**Deploy state:** through `65f503a` deployed; the docs/refactor commits (`3e79a80` …) and
S4 are committed-locally/unpushed — they ride the next `git push all main`.

---

## The three-axis model

- **Selection** — a continuous wall-clock time window the user is browsing, plus the
  sub-window brush. Today: `TimePicker` (Graylog-style) + noUiSlider sub-range +
  click-an-annotation-to-jump. It must become *generic time*, independent of where GPS
  data happens to exist.
- **Layers** — toggleable data drawn on the map and onto the timeline: trail (with a
  *color-by* channel), stops, drone flights, sensor overlays/charts. Today: scattered
  (drone is a one-off panel; sensors live on a divorced page). Formalize into one
  control; this is where "integrate more data into the map" actually lands.
- **Marks** — user-curated points/spans with a name, notes, and optionally a *type*
  (campsite / fuel / scenic / repair). This is what "annotation" should mean — and
  *only* that. Navigation and analysis duties move off it.

---

## Axis 1 — Selection (worked; first up)

### The bug

The timeline slider's domain is built from the **loaded GPS data**, not the requested
window (`static/js/timeline.js`):

```js
const lo = toTs(allPoints[0].timestamp);     // first loaded point
const hi = toTs(allPoints.at(-1).timestamp); // last loaded point
range: { min: lo, max: hi ... }              // axis ≡ data extent
```

…and selection is `pointsInRange(lo, hi)` — a raw filter over loaded points. Axis ≡
data produces two failures, same root cause:

1. **Domain coupling.** Ask for "last 24h" but the van was off for the first 6h → the
   slider only spans the 18h that have points. Empty time is clipped out of existence
   and can't be selected. (Also the source of the deferred **slider-extent bug**: a
   lead-in dwell whose `dwell_start` predates the window drags `lo` earlier than asked.)
2. **Density coupling → dead zones.** A 3-hour park collapses to one `kind='stop'`
   vertex. Dragging the handle across those 3 hours of real time returns that one point
   (or none) — count and map don't change. The axis advances through time that has no
   data on it; the user reads it as broken.

### Design principle

**The timeline axis is continuous wall-clock time = the requested `[from, to]` window,
full stop. Data renders *onto* it; it never defines it.** Stops are first-class blocks,
not zero-width vertices.

### Action items

- [x] **S1 — Decouple the axis from data.** Slider `range.min/max` = the picker's
  `[from, to]`, not `allPoints[0]/at(-1)`. Empty leading/trailing time becomes visible
  and selectable. Resolves the slider-extent lead-in-dwell bug as a side effect.
  *(Landed: `timeline.js` slider domain + `getWindow()`; `annotations.js` slider-overlay
  rebased to the window. Verified headless against `gps_drive.db` — a 24h window with a
  3.5-day lead-in dwell now pins the slider to the window instead of stretching to the
  dwell start.)*
- [x] **S2 — Selection carries context, not a bare filter.** `pointsInRange` now
  selects stops by **dwell-interval overlap** and moving vertices by timestamp, so
  brushing inside a long park reports "parked here" instead of "0 points". *(Landed:
  `timeline.js`. Verified — brushing 06:00–12:00 inside a 3.85-day dwell now selects the
  park. **Still open:** bracketing for narrow moving windows that fall between decimated
  vertices — lower-value, deferred to S4 when the density track lands.)*
- [x] **S3 — Stop blocks, not gaps.** Stops render as blocks spanning
  `dwell_start → dwell_end` on the slider (`#tl-stop-overlay`, hover shows dwell), and as
  **constant-size red dots** on the map (`stop-circle` layer). The backend now ships the
  dwell interval (`points.py`). *(Landed: `points.py`, `timeline.js`, `map.js`,
  `index.html`, `app.css`. Verified headless — 3 blocks, the lead-in dwell spans 89% of a
  24h window. Subsumes the deferred *distinct dwell markers* item. Division of labor
  settled with the user 2026-06-23: the map dot answers **where**, the timeline block
  answers **how long** — so the dot is not dwell-scaled. How/whether to surface dwell on
  the map dot too is left open.)*
- [x] **S4 — Legible density track.** A canvas lane (`#tl-density`) above the slider
  shows a per-pixel-column **coverage fill, density-modulated**, over the window domain:
  stops fill their dwell interval, moving vertices raise the column's density *and*
  bridge the gap to the previous vertex within `DENSITY_GAP_CAP_MS` (15 min) — so a drive
  reads solid (decimation thins straightaways) while a real outage stays an empty gap.
  Bar height/alpha = floor + `sqrt(count)` boost, so maneuvering reads bright, a park
  reads as a low floor (with its red stop block below), and van-off reads as the bare
  recessed track. Window-based (redraws on load + resize, not on brush), DPR-aware,
  `pointer-events: none`. *(Landed: `timeline.js` `renderDensity`, `index.html`,
  `app.css`. Verified headless via CDP at desktop + mobile(dpr 2) widths against
  `gps_drive.db`: a 3.25h window rendered floor→dense-drive→floor→empty-gap, 80% covered
  with a 20% trailing gap.)* **Limitation noted:** the gap-cap heuristic can't perfectly
  tell a sparse straightaway (>15 min between retained vertices) from a real outage; the
  proper fix is a backend coverage summary, deferred. **The Layers handoff:** sensor
  density renders onto this same lane (multi-channel) later.
- [x] **S6 — Couple the picker to the slider.** The time-window picker and the slider
  were two widgets in opposite overlay corners (fetch-window vs. view-brush). User chose
  the lighter "move trigger down" over a full merge: the `#timepicker-trigger` now docks
  in the bottom panel directly above the slider (`.tl-time-row`); the popover opens
  *upward*; `.map-overlay-top` is gone. *(Landed: `index.html`, `app.css`,
  `timepicker.js`. Verified headless at desktop width — popover opens upward, top-left
  clear. Mobile is unchanged: `positionPopover` early-returns and the CSS bottom sheet
  handles it.)*
- [x] **S7 — Zoom to Range.** A "Zoom to Range" button narrows the loaded window to the
  slider's current selection (`TimePicker.setState` range mode), re-fetching at higher
  granularity — the size-aware `/api/points` budget over a smaller window keeps more
  vertices. Enabled only when the selection is narrower than the loaded window (zooming
  to the full window would be a no-op). This is the "promote the brush to a fetch window"
  behavior; full picker presets/custom widen back out. *(Landed: `timeline.js`
  `zoomToRange` + enable-gating, `index.html`, `app.css`. Verified headless — brushing
  then zooming narrowed a 24h window to the selected ~3h.)*
- [ ] **S5 — (bigger, evaluate) Consolidate the strip.** Once the overlay outgrows the
  absolutely-positioned-`#tl-slider-overlay` sibling hack, fold handle + density +
  blocks + bands into one custom canvas/SVG timeline component, retiring noUiSlider.
  Evolve-in-place may keep noUiSlider through S1–S4 and only do this if warranted.

### Touchpoints

- `static/js/timeline.js` — axis domain (S1), selection semantics (S2), strip overlay
  (S4/S5).
- `static/js/map.js` — distinct stop markers (S3).
- `api/routes/points.py` — stops already carry `dwell_start`/`dwell_end`; confirm
  they're in the `/api/points` payload, else add. A lightweight density/summary may be
  derivable client-side from the returned points first.

---

## Axis 2 — Layers (stub)

Formalize the data-on-map axis into one control. Anticipated items:

- One **Layers panel** replacing the scattered toggles; fold the existing 🚁 drone
  panel into it.
- **Trail color-by** — none / speed / elevation / a sensor channel (OBD coolant, RPM,
  cabin temp/IAQ).
- **Sensor on the map** — pull `/sensors` data (`/api/sensors/:id/readings`) onto `/`:
  as a trail colorizer and/or a uPlot chart synced to the Selection window, instead of
  a divorced page. uPlot is already vendored.
- **Stops as a layer** — toggle the distinct stop markers from S3.

Renders onto the continuous Selection axis from S4 (sensor density under the handle).

---

## Axis 3 — Marks (stub)

Slim "annotation" to its real job and split off the other two duties:

- A **mark** = user point/span with name, notes, and optionally a **type** (campsite /
  fuel / scenic / repair). Possibly a `type` column on `annotations`.
- **Analysis moves off marks.** Fuel economy, elevation profile, speed-over-time become
  an **"inspect this window"** panel that works on *any* current selection, not only
  saved ranges. Retire the `loadEconomies` bolt-on on the annotation list.
- **Stops promotable to marks.** A machine-derived `kind='stop'` / `track_events` stop
  can be saved as a mark — collapsing the two parallel "meaningful spot" notions into
  one. (This is the deferred denoise **Phase 6**: surface `track_events` as suggestions,
  promotable to annotations.)
- Marks become a *curated layer*, not the navigation+analysis catch-all.

**Landed early (2026-06-23):** the old "Drop Pin" button — which created a point
bookmark from the slider's arbitrary right-handle time and wrongly implied map
placement — is replaced by **"📍 Bookmark Here"**: a one-tap, form-less point bookmark at
the *latest GPS fix* (current position), shown only when Live (a while-driving action).
Auto-named `Bookmark · <time>`. *(`timeline.js` `bookmarkCurrent`, `index.html`.)*

**Edit/rename UI landed (2026-06-23):** each drawer item now has an **✎ edit** button
beside **×** that opens the shared annotation modal in "Edit" mode (name + notes;
`editId` routes `saveAnnotation` to `PATCH`). This closes the rename gap the auto-named
bookmarks exposed. The row actions reveal on hover (desktop) and stay visible on touch
(`@media (hover: none)` — also fixes delete being unreachable on the phone). *(Landed:
`timeline.js` `openEditAnnotation`, `annotations.js`, `app.css`.)* Bounds-editing
(point↔range) is still out of scope.

---

## Constraints carried from the project

- **Offline-first.** No new runtime deps without vendoring into `static/vendor/`; uPlot
  and MapLibre are already vendored. No CDN at runtime.
- **Mobile-first.** The primary client is a phone browser over the van's WiFi; the
  redesign must work as a bottom-sheet / touch layout, not just desktop.
- **Read-only frontend.** GPS logging and the processor are untouched; this is all in
  `api/routes/*` reads + `static/` + `templates/`.
- **Processed tier is the source.** The trail reads `track_points` via `/api/points`
  (size-aware decimated); stops already carry dwell intervals. Don't reach back into raw.

---

## Codebase touchpoints (anticipated)

- **`static/js/timeline.js`** — the Selection axis rework (S1–S5).
- **`static/js/map.js`** — stop markers, trail color-by, sensor overlay.
- **`static/js/annotations.js`** — slim to marks; drop `loadEconomies`.
- **`static/js/sensors.js` / `api/routes/sensors.py`** — reuse the readings path to put
  sensor data on `/`.
- **`api/routes/points.py`** — ensure dwell bounds in the payload; possible window-
  summary endpoint for the "inspect window" panel.
- **`api/db.py` + `api/routes/annotations.py`** — optional `type` column on marks.
- **`.claude/modules/frontend.md`** + `CLAUDE.md` — update as each axis lands; drop this
  plan when all three have folded in.
