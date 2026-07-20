# Systems IA rework — hub, section-nav, glance↔graph fusion

**Status:** shaping → execution. Frontend-only (Svelte SPA). No API changes needed
(the series/registry endpoints already carry everything).

## Problem

`/systems` does two unrelated, both-growing jobs: a **status dashboard** (one card per
sensor node, overlapping Home's glance) and a **tool launcher** (a flat `.diag` button
list buried at the bottom of the scroll). Graphing (Trends) is mis-filed there as a
"diagnostic". And the same "one tab → several destinations" idea is solved three
different ways with no shared component (Systems `.diag` list, `Sky.svelte` `.views`
strip, Home `.health` pills) — so there is no *system*, which is the actual ask.

Deeper: the glance cards and Trends are two views of the **same channels**, with no
bridge. Simple channels (battery %, cabin temp) read fine as a number; the harder ones
(coolant, HaLow RSSI, IAQ, DC load) only mean something against time.

## Locked decisions (from the user)

1. **Systems = hub.** Landing is a tile grid with live status + a reusable **sticky
   section-nav** on every sub-page. Same component later serves Sky.
2. **"Sensors" is its own destination**, off the landing — kills the card clutter and the
   Home overlap.
3. **Trends → top-level nav tab** (9 tabs total). Watch bottom-bar label width — the
   `min-width:0`/ellipsis trap already bites at 8 (frontend.md).
4. **Sensors cards carry inline sparklines** on every `chart:true` metric; tap any
   metric/sparkline to expand into full Trends preloaded on that channel.

## Target IA

- **Home** — unchanged: the curated cross-domain glance (`/api/status`).
- **Trends** — top-level tab (`/trends`), 📈. Graph any channel. Also the natural
  expand target of a Sensors sparkline.
- **Systems** (`/systems`) — a **hub**: tile grid, each tile a destination with a live
  one-line status + dot. Tiles: Sensors, Trends (cross-listed), Fridge, Data, Time
  (NTP), GPS (gpsd). Sticky section-nav above the tiles and on every sub-page.
- **Sensors** (`/sensors`, tab `/systems`) — the per-node telemetry, moved off the
  Systems landing. Value + inline sparkline per `chart:true` metric; tap → Trends.

## Reusable components (the "system")

- **`SectionNav.svelte`** — a sticky segmented strip of `{label, to}` pills, highlighting
  the current route. Rendered **shell-level**, keyed by the route's `tab`, from a section
  registry — so every sub-view gets it with no per-view wiring, and Sky adopts it by
  registering its own section. Replaces Systems `.diag`, Sky `.views`.
- **`SectionHub.svelte`** — the tile grid: `{label, to, status?, dot?}[]`. The Systems
  landing renders this. Tile statuses reuse the already-polled `/api/status` +
  `/api/sensors` aggregates (fridge temp/power, gps fix/sats, ntp synced all live in
  `/api/status`; sensors count/liveness in `/api/sensors`) — minimal new fetching; only
  the Data tile's freshness dot would need `/api/data/status`.
- **`charts/Sparkline.svelte`** — pure inline SVG (normalized `<path>` + optional
  min/max), **not** LayerCake. Cheap enough for dozens per page, offline, Vitest-able
  pure helper (`charts/util.ts`). Distinct from the lazy LayerCake `Trend.svelte` used
  for the full expand.

## Data paths & traps

- **Sensors sparklines = one batched call.** `getSensorSeries(allChartableAddrs, from,
  to, ~60buckets)` over a fixed recent window; the server fans out one query per sensor
  internally. Do **not** fetch per sparkline. Current values still come from the existing
  `getSensors()` (latest) that Systems already used.
- **Sparkline window** — fixed recent (default **last 12h**) so the page is
  self-contained, independent of wherever Trends' global Selection axis was last zoomed.
  (Open sub-decision: a small 6h/24h/7d toggle later.)
- **Tap-to-expand handoff** — a tiny `stores/trends.svelte.ts` (or a `pending` field):
  set `pending = [addr]`, navigate `/trends`; Trends `onMount` consumes it in place of
  the Victron default. Mirrors `layers.pendingZoom`. Leaves the global window alone
  (changing it also drives the Map — a surprising side effect for a tap).
- **`chart:true` gate** — the same `metricMeta(meta,key).chart` flag Trends uses decides
  which metrics get a sparkline + tap target. Non-chartable (enum/coded) stay value-only.

## Phases (incrementally shippable)

- **P1 — Trends to a top-level tab.** Add to `NAV` (📈), flip its route `tab`
  `/systems`→`/trends`. Ships the headline "graphing surfaced" win alone. Verify 9-tab
  label width on-device.
- **P2 — SectionNav + SectionHub + section registry.** Shell-level SectionNav keyed by
  tab; Systems landing → SectionHub. Fold Sky's `.views` onto SectionNav.
- **P3 — Split Sensors out.** New `Sensors.svelte` = today's per-node card dump; route
  `/sensors`. Systems.svelte becomes hub-only.
- **P4 — Inline sparklines.** `charts/Sparkline.svelte` + batched series fetch on
  Sensors; tap → `trends.pending` → `/trends`; Trends consumes the handoff.
- **P5 — Docs.** Update `.claude/modules/frontend.md` (new IA, SectionNav/SectionHub
  pattern, Sparkline vs Trend split, tap-to-graph handoff) + sensors.md cross-ref; drop
  this plan.

## Open sub-decisions (flag, don't block)

- Cross-list Trends as a Systems hub tile / section-nav item even though it's now a
  top-level tab? (Leaning yes — Sensors→Trends is the natural drill.)
- 9 tabs on a narrow phone: acceptable, or does a tab need shortening/merging?
- Sparkline window: fixed 12h vs a small span toggle.
