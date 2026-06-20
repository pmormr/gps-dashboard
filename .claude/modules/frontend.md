# Frontend

Plain files in `static/` and `templates/`, all JS/CSS vendored in `static/vendor/`
(no CDN at runtime), mobile-first (the primary client is a phone browser). One
map-centric view at `/` plus four standalone status/viewer pages. The MapLibre map,
the ⚙ Labels panel, and the 🏔 3D/terrain panel are documented in
`.claude/modules/basemaps.md`.

## Map view (`/`)

A single MapLibre map (`MapView`, `static/js/map.js`) with time-range controls on top:

- **Time picker** (`TimePicker`, `static/js/timepicker.js`) — Graylog-style. Modes
  Last / Around / From→To with anchor + window state; preset chips
  (15m/1h/6h/24h/7d/30d) collapse to Live + Last. A Live flag pins the anchor to
  `now()` and re-fetches every 30s.
- **Sub-range slider** (`noUiSlider`) — zooms inside the loaded window. The trail
  polyline + map-fit follow the slider's selection; in live re-fetches `fitBounds`
  is skipped so the view doesn't jerk.
- **Annotations drawer** — right-edge drawer on desktop, bottom sheet on mobile,
  toggled from the tab bar. Lists points + ranges; click jumps the picker (range →
  `range` mode, point → `around` mode keeping the current window) and pans to the
  nearest fix. Map overlays: cyan polylines for in-window ranges, amber pins for
  in-window points; matching bands + ticks on the slider.
- **Creation** — "Create Range" uses the slider's `[lo, hi]` (≥2 points); "Drop Pin"
  captures the slider's `hi` handle (or `now` in live).
- **⊕ FAB** — zooms to the most recent GPS fix.

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
