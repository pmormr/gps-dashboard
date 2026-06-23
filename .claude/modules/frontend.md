# Frontend

Plain files in `static/` and `templates/`, all JS/CSS vendored in `static/vendor/`
(no CDN at runtime), mobile-first (the primary client is a phone browser). One
map-centric view at `/` plus four standalone status/viewer pages. The MapLibre map,
the ⚙ Labels panel, and the 🏔 3D/terrain panel are documented in
`.claude/modules/basemaps.md`.

## Map view (`/`)

A single MapLibre map (`MapView`, `static/js/map.js`) with a time panel docked at the
bottom — picker + slider together (they were split top/bottom before the map-view
redesign):

- **Time picker** (`TimePicker`, `static/js/timepicker.js`) — Graylog-style trigger
  docked above the slider; its popover opens *upward*. Modes Last / Around / From→To
  with anchor + window state; preset chips (15m/1h/6h/24h/7d/30d) collapse to Live +
  Last. A Live flag pins the anchor to `now()` and re-fetches every 30s.
- **Sub-range slider** (`noUiSlider`) — its axis is the **requested `[from, to]`
  window**, not the loaded-data extent, so empty time stays selectable and a lead-in
  dwell can't stretch it. Brushing zooms the trail/map within the window (local, no
  fetch; `fitBounds` skipped on live ticks so the view doesn't jerk). Stops in the
  window render as **dwell-interval blocks** on the track (`#tl-stop-overlay`, hover
  shows dwell). **Zoom to Range** promotes the brushed selection to a tighter fetch
  window — more detail, since `/api/points` is size-aware decimated.
- **Annotations drawer** — right-edge drawer on desktop, bottom sheet on mobile,
  toggled from the tab bar. Lists points + ranges; click jumps the picker (range →
  `range` mode, point → `around` mode keeping the current window) and pans to the
  nearest fix. Each item has **✎ edit / × delete** (revealed on hover; always shown on
  touch via `@media (hover: none)`); edit reuses the create modal in "Edit" mode
  (`PATCH`). Map overlays: cyan polylines for in-window ranges, amber pins for in-window
  points, **constant-size red dots for stops**; matching bands + ticks on the slider.
- **Creation / bookmarks** — "Create Range" makes a range annotation from the slider's
  `[lo, hi]` (≥2 points); **"📍 Bookmark Here"** (Live only) one-taps a point bookmark at
  the latest GPS fix, auto-named `Bookmark · <time>`.
- **⊕ FAB** — zooms to the most recent GPS fix.
- **🚁 Drone panel** (`static/js/drone.js`) — toggles the drone-track overlay. On
  first enable it fetches *all* flights once from `GET /api/drone/flights` (tiny
  dataset; independent of the time picker) and renders them as the `drone-line`
  layer, colored per model (Mini 5 Pro / Avata 2 / Neo). Click a track → a popup
  with model, time span, `abs_alt` range, and media path. v1 drapes the tracks flat
  on the terrain (no elevated-line support in the vendored MapLibre).

**View behavior.** Default view is Live, last 24h, centered on the most recent fix. The
map re-centers **only** when Live is on or an annotation is clicked — otherwise the user
pans/zooms freely (browsing ≠ navigation). Live is offered only in `last` mode (an
`around`/`range` Live window would extend into the future).

**Decimation is server-side and size-aware (C17):** the client always requests
`limit=20000`; the `/api/points` handler keeps every stop plus the highest-
`importance` moving vertices (see the API Endpoints section in CLAUDE.md). The old
client-side `?bucket=` time-bucketing is gone — the processed tier is already sparse.

## Standalone pages

- `/gpsd` — gpsd service state, fix mode, satellite count, latest coordinates,
  pass/fail indicators.
- `/skyplot` — live 3D satellite skyplot (`static/js/skyplot.js`). Polls
  `/api/gpsd/sky` every ~4s and renders the visible constellation on a draggable,
  tilted wireframe hemisphere in plain canvas (no 3D lib — stays offline). Satellites
  placed by az/el, depth-sorted with a stem to the dome floor, colored by
  constellation, filled = used / hollow = visible, sized by SNR. Drag to orbit/tilt;
  "Top-down" sets tilt 90° for the classic flat azimuth plot. A van glyph at dome
  center is oriented to the GPS heading (falls back to an observer dot when stopped).
  Legend chips tap-to-toggle each constellation; "Trails"/"Vectors" overlay each
  satellite's trajectory and a moving-average direction arrow. DOP is illustrated
  three ways: always-on H/V/PDOP gauge bars (zoned by quality) + a colored Quality
  label, a toggleable "Footprint" (horizontal error ellipse on the dome floor from
  xdop/ydop + a VDOP pillar, colored by PDOP band), and a toggleable "Geometry" hull
  (dashed convex hull of the used satellites). Toggles persist in `localStorage`;
  motion history accumulates client-side (pruned ~5 min) — the server stores nothing.
- `/ntp` — chrony sync status, stratum, offset, GPS/PPS source state, LAN server status.
- `/sensors` — per-sensor current values + trend charts (`static/js/sensors.js`,
  vendored uPlot), polling `/api/sensors` and `/api/sensors/:id/readings` every 30s.
  Reads the logged DB — no live broker — so it works regardless of broker websockets.
  Range buttons (1h/6h/24h/7d) and a per-sensor liveness dot (online/stale/offline).

`/gpsd` and `/ntp` auto-refresh every 30s via `<meta refresh>`; `/skyplot` and
`/sensors` poll in place (a full reload would drop canvas/chart state).

## Deferred

- Richer trail rendering: color-by-speed, direction chevrons, head dot.
- Per-annotation elevation profiles + speed-over-time charts (uPlot is already vendored
  from `/sensors`).
- Annotation-list pagination — hundreds of annotations render fine without it.
