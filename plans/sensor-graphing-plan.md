# Sensor Graphing Engine

A configurable trend-graphing engine for the sensor platform: view any sensor
metric over an arbitrary window (e.g. Victron `battery_voltage` over the last 2
weeks), **overlay multiple metrics** on a shared time axis (voltage + current,
voltage + temperature — including *cross-sensor*), and — because it reads the
global Selection store — scope a chart to a particular drive/trip.

This implements the long-standing `frontend.md` Deferred item *"a uPlot chart
synced to the Selection window (retire the divorced /sensors)"* and supersedes
the legacy `/sensors` Jinja page.

## Where things stand

- **Legacy `/sensors`** (`templates/sensors.html` + `static/js/sensors.js` +
  vendored `static/vendor/uplot`) — per-sensor stacks of *single-metric* charts,
  fixed range buttons, 30 s poll. No overlay, no cross-sensor, not tied to the
  map's time window. Un-ported to the SPA.
- **SPA Systems tab** — current-values grid only; links *out* to the legacy page.
- **`GET /api/sensors/<id>/readings`** — one sensor, all columns, **no
  downsampling**; caps at 20 k rows then truncates (2 weeks of 30 s Victron ≈
  40 k rows → silent truncation). Stays for the legacy page until Phase 3.
- **`METRIC_META`** (`api/sensor_schema.py`) already carries per-column label /
  unit / dec / color / convert / y_range / group, served by `/api/sensors`.
  The engine renders entirely from it — no parallel presentation map.
- **`selection` store** (`web/src/lib/stores/selection.svelte.ts`) — the app's
  global time axis. The Map drives off it; clicking an annotation/range already
  sets it. Pointing the chart at the same store is what makes trip-scoping work
  with no extra plumbing.

## Locked decisions

- **Placement:** a Systems drill-in at **`/trends`** (mapped to the Systems
  tab), not a 7th top-level nav destination.
- **Data path:** a new **server-side bucketed series endpoint** — time-bucketed,
  downsampled, aligned across sensors. Mirrors the `/api/points` size-aware
  decimation philosophy and bounds long windows.
- **Aggregation:** **avg per bucket** in Phase 1. Min/max envelope (spike
  preservation for voltage sag / RPM) is a Phase 2 option, not the default.
- **Smoothing:** a **client-side moving-average** control (default off), distinct
  from bucket aggregation — it tames noisy channels (fuel-level slosh) at short
  windows where bucketing barely collapses anything. Per-metric smoothing
  defaults (so `fuel_level_pct` smooths by default) land in Phase 2.
- **Charting lib:** **LayerCake** (Svelte-native layout engine — scales +
  dimensions, you compose chart *layers* as Svelte components). Fits the
  project's hybrid-Svelte ethos far better than wrapping an imperative lib, and
  the server-side bucketing (~1–2 k points/series) defuses uPlot's only real
  edge (raw-point throughput). The trade: LayerCake is bring-your-own-marks, so
  Phase-1 basics (axes/tooltip/legend) are more upfront code — but the
  compositing-heavy Phase 2/3 work (min/max band, trip brush, annotation bands
  on the chart) gets *much* easier. Deps to add: `layercake` + `d3-shape`
  (line/area generators); LayerCake bundles `d3-scale`. Lazy-import into its own
  chunk. *Note: `frontend.md` claims uPlot is an npm dep — it isn't; correct that
  line (the legacy `/sensors` still uses the vendored uPlot until Phase 3).*

## The endpoint — `GET /api/sensors/series`

Metric-addressed, bucketed, uPlot-native shape.

**Params**
- `metrics` (required) — comma-separated `<sensor_id>.<column>` addresses, e.g.
  `3.battery_voltage,3.battery_current,1.temp_c`. Validate each: sensor exists,
  column ∈ that type's `READING_TABLES` metrics. (The picker only offers
  `chart:true` columns; the endpoint itself accepts any numeric column.)
- `start` / `end` — via `api.params.parse_time`; default trailing 24 h (like
  `/readings`).
- `buckets` (optional, default ~1000, cap ~2000) — target point count across the
  window. Server derives `bucket_ms = max(ceil(window_ms / buckets), 1000)`.

**Response** (client builds uPlot data = `[x, s0.values, s1.values, …]`)
```jsonc
{
  "start": "...", "end": "...", "bucket_ms": 60000,
  "x": [t0_ms, t1_ms, ...],            // dense bucket-start grid, epoch ms
  "series": [
    {
      "metric": "3.battery_voltage", "sensor_id": 3, "column": "battery_voltage",
      "label": "Battery V", "unit": "V", "color": "#facc15", "dec": 2,
      "convert": null, "y_range": null, "group": "battery",
      "values": [13.4, 13.42, null, ...]   // aligned to x; null = empty bucket
    }
  ]
}
```

**Bucketing.** Canonical timestamps are ISO ms-UTC strings, so bucket on epoch
seconds: `CAST(strftime('%s', timestamp) AS INTEGER)` → `GROUP BY (epoch_s /
bucket_s)`, `AVG(col)` per metric (ms precision is irrelevant for ≥1 s buckets).
One query per distinct `(table, sensor_id)` covering all that sensor's requested
columns (bounds query count to #sensors, not #metrics); scatter results into a
**dense** `x` grid (`start … end` step `bucket_ms`) by bucket index, nulls for
empty buckets so gaps render as line breaks. Presentation fields come from
`METRIC_META`. Phase 2 adds `min`/`max` arrays alongside `values` for the
envelope.

## Client pieces

- **`web/src/lib/charts/`** — LayerCake chart components, composed not wrapped:
  `Trend.svelte` (the `<LayerCake>` container + shared scales) plus layer
  components `Line.svelte`, `AxisX.svelte`, `AxisY.svelte`, `Tooltip.svelte`
  (and later `Band.svelte` for the min/max envelope, `Brush.svelte` for the
  trip sub-window). Reactive to props/stores directly — no imperative façade.
  Phase 1: series sharing a unit share a y-scale (simple). The pure bits —
  client moving-average smoothing + the value→aligned-series merge — live in
  `web/src/lib/charts/util.ts` so they're unit-testable.
- **`web/src/lib/api.ts`** — `getSensorSeries(metrics, start, end, buckets?)` +
  response types.
- **`web/src/views/Trends.svelte`** — the explorer: a metric picker (grouped by
  sensor/domain → `chart:true` columns, from `/api/sensors`), the existing
  `TimePicker` bound to the global `selection` store, a smoothing control, and
  the overlay chart. Lazy-import `uplot` so it's its own chunk.
- **`web/src/lib/routes.ts` / `Shell`** — register `/trends` under the Systems
  tab; link to it from `Systems.svelte` (replacing the external "History &
  charts" link).

## Phasing

**Phase 1 — engine + explorer.** `/api/sensors/series` (avg buckets) +
`charts/` LayerCake components + `Trends.svelte` (multi-metric overlay, metric
picker, global time axis, basic client smoothing toggle) + `layercake`/`d3-shape`
npm deps + `/trends` route/link. Retire nothing.

**Phase 2 — fidelity + ergonomics.** Min/max envelope option (spike
preservation); multi-unit y-axes (voltage + current on separate scales);
per-metric smoothing defaults from `METRIC_META` (`fuel_level_pct` smoothed by
default); saved metric presets (localStorage first).

**Phase 3 — map tie-in + cleanup.** Dock the chart under the Map synced to the
same Selection window (the Layers Axis-2 *"inspect this window"* item, which
generalizes the per-range `/api/obd/economy` readout to any window). Then retire
legacy `/sensors` + `templates/sensors.html` + `static/js/sensors.js` +
`static/vendor/uplot`; fix the `frontend.md` uPlot-is-an-npm-dep line.

## Testing

- **Endpoint** (Flask client vs. the temp SQLite DB, `tests/conftest.py`):
  bucketing + dense-grid alignment, empty-bucket nulls, multi-sensor address
  parsing, validation/4xx on bad `sensor_id.column`, default window, `buckets`
  cap. Sits beside the existing `/api/points` decimation and sensor tests.
- **Client:** the pure helpers in `charts/util.ts` (moving-average smoothing +
  the value→aligned-series merge) get unit tests; the moving-average is the
  load-bearing pure bit. The LayerCake layer components are presentational.

## Open / deferred

- Per-series y-axis assignment beyond unit-grouping (left/right, normalized) —
  Phase 2.
- Saved chart presets persisted server-side (a small table) rather than
  localStorage — only if presets prove worth syncing across devices.
- A `chart:true` vs. numeric-but-stateful distinction already lives in
  `METRIC_META`; revisit if a wanted channel is currently `chart:false`.
