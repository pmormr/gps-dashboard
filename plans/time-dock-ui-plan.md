# Time Dock + Map Chrome Rework

**Status: Phases 1–4 landed + deployed (2026-07-02); next: Phase 5.**
Phase 4 notes: rail panels default closed and reset on remount (a clean map each
visit); legend chips sit under the top-left annotations cluster. Revised in
review: Data layers (🛰) and Map style (🎨) are separate rail buttons — not one
Layers panel with a collapsed style group (unrelated tasks).
Landed beyond plan: a Go Live nav button (reset to Live · Last 24h, history
cleared) — shown whenever the axis is frozen.
Phase 2 notes: wheel commits go through `zoomTo` (one history entry per gesture
pause) so double-click backs out of wheel zooms too.
Phase 3 notes: annotation band/tick clicks use a 250ms delayed-click guard so
double-click (back) cancels the jump — the same guard unlocks the deferred
stop-dwell-block click→zoom nice-to-have if wanted. Trends' mobile dock collapse
(picker+nav only) not done — revisit if vertical space actually hurts.
Execute phases in order; Phases 1–2 are one deployable unit (the picker and strip
change together — don't ship one without the other). Every UI phase ends with
`npm run check` + Vitest, a rebuild of `static/dist/`, and a commit.

Unify time navigation around the global Selection window (one interaction grammar
shared by Map and Trends), and replace the map's right-side panel stack with an
icon rail. Motivating workflow: select a trip segment on the map → pivot to OBD
for that window → pivot to cabin temperature — the window carries, the control
looks the same everywhere.

## Locked decisions (2026-07-02 discussion)

- **Desktop-primary.** Phone is for quick checks, not analysis; when a tradeoff
  bites, desktop wins (keyboard, wheel, horizontal room for the strip).
- **The Selection window is the only time object.** The timestrip's two-handle
  sub-brush dies. Rationale: everything else (map trail fetch, Inspect, phone
  layer, Trends, annotation jumps) already keys off the window; the brush was the
  lone second-class selection, and its two buttons (Zoom to Range / Create Range)
  existed only to promote it. Trends semantics everywhere: **drag = zoom**
  (refetch — size-aware decimation means zooming *is* how you get detail).
- **`around` mode dies.** "Jump to a date + zoom" covers it.
- **Shared TimeDock chrome** rendered by both Map and Trends (same component,
  same store), not map-local.
- **Right side → icon rail, exclusive-open panels.** Only one panel open at a
  time; Layers splits *data layers* (frequent toggles) from *map style* (rare);
  legends move out of the panel to on-map chips shown only while the layer is on.
- **Annotations are named windows.** The schema always said so (pure time
  metadata, no FK — any tier replays against the bounds); the UI now says so too.
  A range annotation = a saved window ("day 3 of the road trip") that restores
  exactly and scopes *every* consumer (trail, OBD, cabin temp); a point
  annotation = a named moment that restores a window centred on it. Creating one
  is literally naming the current window ("Save window"). Consequence: the
  annotation *list + jump* graduate from map-local to axis-level (usable from
  Trends); only the map rendering (pins/polylines, pendingPan) stays map-side.

## What's lost (accepted)

- Previewing a highlighted sub-segment in the context of a wider window without
  refetching. (Rarely used; drag-zoom + back-stack replaces it.)
- Creating a range annotation looser than the current window: you now zoom the
  window to the trip, then save it. AnnotationForm still edits bounds after.

## Phase 1 — Selection model simplification

Store (`web/src/lib/stores/selection.svelte.ts`):

- Drop `'around'` from `Mode`; `range` getter loses the around branch.
- Delete the brush state: `brushLo/Hi`, `brush`, `setBrush`, `isSubRange`
  (`setLoaded`/`loadedFrom/To` go too if nothing else needs them).
- Move Trends' `zoomStack` into the store: `zoomTo(from, to)` (push current
  `pickerState`, then `setRange`), `back()`, `resetZoom()`, `canGoBack`.
- Navigation helpers: `shift(dir: -1 | 1)` (move by one window-width; leaving
  Live when shifting back), `widen()` (2× around center, capped). No `narrow()`
  button — drag covers it (decided).
- Live semantics with history: `zoomTo` forces Live off (it's a `setRange`), but
  the pushed `pickerState` snapshot preserves `live: true` — so `back()` out of
  a zoom *resumes* Live. Intended; keep it.

Consumers:

- **Trends**: delete the local `zoomStack`; wire chart `onzoom`/`onresetzoom` +
  the ⊖ button to the store history.
- **Annotations** (`annotations.svelte.ts`): `jumpTo` for a point annotation is
  the only real `around` consumer — replace with an explicit
  `setRange(ts − w/2, ts + w/2)` keeping the current window size.
- **TimePicker rewrite** (`TimePicker.svelte`): no mode tabs, no Around, no
  Last-window form. Trigger + compact popover: preset chips (immediate), Live
  toggle, From→To datetime pair for absolute jumps. Everything else is direct
  manipulation on the strip.

## Phase 2 — Strip becomes drag-to-zoom

`web/src/lib/timestrip.ts`:

- Remove the two-handle brush, middle-pan, tap-to-jump-handle, and the dim mask.
- Drag = rubber-band selection → on release, `selection.zoomTo(...)`.
- Wheel over the strip = zoom in/out centered on cursor time; double-click =
  `back()`. Keyboard: ←/→ shift, +/− zoom, Backspace back. **Debounce the wheel:**
  redraw the pending domain immediately per step, but commit to the store (and
  thus refetch) only after ~200 ms of wheel idle — otherwise every notch fires a
  `/api/points` fetch.
- Keep: density coverage, stop dwell blocks, annotation bands/ticks, tooltips.
- The strip always renders (axis + annotation bands) even with zero points in
  the window — today `tl-strip-wrap` hides on empty, but an empty-density axis
  is still navigable, and clickable annotation bands (Phase 3) must stay
  reachable from a dataless window.
- Nice-to-have (defer if fiddly): click a stop's dwell block → zoom to its dwell.

`Timeline.svelte`:

- Delete brush plumbing (`renderRange` sub-selection, `pointsInRange` gating,
  `canZoom`/`canCreate`, "Zoom to Range"). Trail always renders the full window.
- Permanent action row shrinks to: **Save window** (range annotation from the
  current window) + **📍 Bookmark Here** (Live only) — small, right-aligned.
- Nav cluster appears next to the picker: `◀ ⊖ ▶` + `↩` (back, shown when the
  stack is non-empty) + Live indicator.

## Phase 3 — Shared TimeDock

- Extract `TimeDock.svelte` (picker trigger + nav cluster + strip + status) from
  the map Timeline. Map renders it as the bottom overlay (as today); Trends
  renders it in place of its `.bar` TimePicker row, above the chart.
- Density data decouples from the map: promote the `/api/points` window fetch
  into a shared store (`track.svelte.ts` — points + truncated + status), consumed
  by the map trail, the dock's density lane, and Inspect-adjacent readouts. On
  Trends the dock shows the same GPS density — useful context ("when was I
  driving") even with no map on screen.
- Mobile: dock stays on Map (bottom bar); on Trends it can collapse to just the
  picker trigger + nav if vertical space hurts.
- **Annotations in the dock** (annotations-as-windows payoff): the strip already
  draws annotation bands/ticks — make them clickable jump targets (click a band
  → restore that window), which works on *both* views once the dock is shared.
  The annotations list/jump moves axis-level so Trends can reach it (e.g. a
  saved-windows section in the picker popover); the AnnotationsDrawer stays the
  Map's management UI (map overlays, edit/delete).

## Phase 4 — Right rail (map chrome)

- Replace the `.map-chrome-tr` panel stack with a vertical icon rail: 🗺 Layers,
  🚩 Marks, 📊 Inspect. One panel open at a time (opening one closes the rest);
  desktop = anchored card next to the rail, mobile = bottom sheet.
- `Layers.svelte` restructure: **Data layers** first (drone, phone, and the
  deferred color-by/sensor/stops layers land here), **Map style** (base map,
  labels, terrain) second, collapsed by default.
- Legends (drone models, phone modes) → small on-map legend chips, visible only
  while that layer is on.
- Annotations button stays top-left with the ⊕ FAB (decided): it opens a drawer
  over curated data, not a map-control panel — a different kind of object than
  Layers/Marks/Inspect.

## Phase 5 — Time↔space linkages

- **Hover-scrub:** hovering the strip shows a ghost dot on the map at that
  moment's position (time → space).
- **Filter to map view:** a toggle that passes the current viewport as `bbox` to
  `/api/points`, so the strip's density shows only time spent in view — "when
  was I ever at this campsite" (space → time). API already supports it.
- Deferred beyond this plan: multi-source density lanes (phone/drone/OBD ticks),
  shell-level dock on more views.

## Verification

- Vitest for the store (history/shift/widen math) and strip selection geometry.
- `npm run check` + existing suites; manual pass on desktop + phone widths.
- Rebuild + commit `static/dist/` before any `git push all` (Pi never builds).
