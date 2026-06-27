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
  docked above the strip; its popover opens *upward*. Modes Last / Around / From→To
  with anchor + window state; preset chips (15m/1h/6h/24h/7d/30d) collapse to Live +
  Last. A Live flag pins the anchor to `now()` and re-fetches every 30s.
- **Timeline strip** (`TimeStrip`, `static/js/timestrip.js`) — the whole sub-range
  timeline drawn in **one canvas** (it replaced noUiSlider + the DOM overlay hacks in the
  redesign's S5). Its axis is the **requested `[from, to]` window**, not the loaded-data
  extent, so empty time stays selectable and a lead-in dwell can't stretch it. One draw
  pass layers: a **density coverage fill** (stops fill their dwell interval; moving
  vertices raise the column's density and bridge gaps under `DENSITY_GAP_CAP_MS`/15 min;
  `sqrt(count)` height/alpha — a drive reads bright, a park a low floor, van-off the bare
  track — answering *where data is / parked / genuinely empty*), **red stop-dwell blocks**
  (bottom lane), **annotation range bands + point ticks** (top lane, fed by
  `setAnnotations`), a **dimmed mask** over the unselected time, and the **two brush
  handles**. Pointer Events drive the brush (drag a handle to resize, drag the middle to
  pan, tap empty track to jump the nearest handle); arrow keys nudge it; hover shows a
  stop/annotation tooltip. The plot area is inset (`EDGE`) so full-extent handles stay
  grabbable; `touch-action:none` keeps a finger drag from scrolling. Brushing re-renders
  the trail/map locally (no fetch; `fitBounds` skipped on live ticks). **Zoom to Range**
  promotes the brushed selection to a tighter fetch window — more detail, since
  `/api/points` is size-aware decimated. The Layers axis will render sensor density onto
  this same canvas.
- **Annotations drawer** — right-edge drawer on desktop, bottom sheet on mobile,
  toggled from the tab bar. Lists points + ranges; click jumps the picker (range →
  `range` mode, point → `around` mode keeping the current window) and pans to the
  nearest fix. Each item has **✎ edit / × delete** (revealed on hover; always shown on
  touch via `@media (hover: none)`); edit reuses the create modal in "Edit" mode
  (`PATCH`). Map overlays: cyan polylines for in-window ranges, amber pins for in-window
  points, **constant-size red dots for stops**; matching bands + ticks on the strip.
- **Creation / bookmarks** — "Create Range" makes a range annotation from the brush's
  `[lo, hi]` (≥2 points); **"📍 Bookmark Here"** (Live only) one-taps a point bookmark at
  the latest GPS fix, auto-named `Bookmark · <time>`.
- **⊕ FAB** — zooms to the most recent GPS fix.
- **🚁 Drone panel** (`static/js/drone.js`) — toggles the drone-track overlay,
  rendered floating at flight altitude via the three.js `Overlay3D` layer. The
  subsystem (source, importer, tier, overlay) is documented in
  `.claude/modules/drone.md`.

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
