# Drive View Plan

## Context

A **"currently driving" view** — a follow camera plus a driving HUD — as the first
step toward navigation. The map centers on the van as it moves, course-up, with the
essentials readable at a dash-mount glance: speed, heading, altitude, engine vitals,
and a straight-line bearing to a chosen destination.

The core loop: fetch the freshest fix + heading, ease the camera (center on van,
bearing = course, pitched), and animate the puck between fixes so it glides instead
of teleporting. Everything else layers on top of that loop.

Deliberately **not** in scope: turn-by-turn routing. True offline routing means a
routing engine (Valhalla/OSRM) + a graph extract on the NVMe — its own plan later.
The navigation seed here is the **destination chevron**: straight-line distance +
bearing-relative arrow to a picked point. No router, works everywhere off-grid.

Treat this doc as the durable, living plan — check items off as they land, record
decisions inline.

---

## Confirmed decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Placement | **Dedicated `/drive` view** (new nav destination) | Uses the shared keep-alive map host (`mapHost.ts`), so a separate route is cheap. The Map view keeps its history-centric identity (timestrip, windows); driving gets its own chrome. Follow-camera logic lives in a lib module the Map view could reuse later. |
| 2 | Live transport (v1) | **1 Hz poll + client interpolation** | New lightweight gpsd-backed endpoint polled every second; the client tweens puck + camera between fixes. Simple, stateless. Consumers read a live-position store, so an SSE upgrade later is a transport swap, not a rewrite. |
| 3 | Position source | **gpsd direct, not the DB** | A live view shouldn't wait on the logger's write path. TPV carries lat/lon, `speed`, `track` (COG), alt, climb, mode in one payload — `common/gpsd.py` already knows how to snapshot it. |
| 4 | HUD scope (v1) | **Speed + heading + altitude, OBD strip, destination chevron, breadcrumb trail** | All four in the first cut. |
| 5 | Destinations (v1) | **Attractions + dropped pins** (not annotations) | Annotations are time ranges with no coordinates — "navigate to an annotation" needs a resolve-to-point step. Defer until missed. |
| 6 | gpsd read pattern (was open A) | **Per-request snapshot, TPV-only fast path** | The receiver runs 5 Hz always (the logger throttles writes, not the module), so the first TPV lands ≤ ~200 ms after WATCH. `query_gpsd()` grows a `want_sky=False` param that returns on the first TPV — no new access mode. A watcher thread would re-implement the logger's reconnect/staleness machinery inside Flask for marginal gain at 1 Hz; revisit only with SSE. |
| 7 | OBD strip data path (was open C) | **Reuse `/api/status` at ~5 s** | OBD reaches the DB via MQTT ingest anyway — a "live" endpoint wouldn't be fresher than the ingest cadence. The HTTP poll isn't the bottleneck. |
| 8 | Breadcrumb seed read (was open D) | **New `GET /api/points/recent?minutes=&limit=`** (raw tier, stride-decimated) | Keeps `/api/points`' documented processed-tier contract (importance-budgeted decimation) clean. The trail is cosmetic — stride decimation suffices, no Reumann–Witkam at read time. |
| 9 | Nav entry | **8th top-level tab** | Crowded on phone bottom tabs but consistent; a driving view deserves top-level reach. Demote to a Map/Home affordance later if it's too tight. |

---

## Open decisions (deferred — refine when we continue)

| # | Decision | Options | Notes |
|---|----------|---------|-------|
| B | Camera feel | Zoom curve breakpoints, pitch angle, ease duration | Land Phase 2 with named constants in `follow.ts` (start ~50° pitch, z16 crawling → z12 highway, ~1 s ease, any pan/rotate suspends + recenter pill) and tune on the road. |

---

## Traps (identified up front)

1. **Heading gate.** gpsd `track` is Doppler course-over-ground — noise below
   ~1 m/s. The live store must hold the last good bearing below a speed threshold
   or the camera spins at every stoplight. (The ICM-20948 compass —
   `plans/motion-imu-plan.md` Phase 1 — is the eventual parked-heading fix; the
   gate is the v1 answer.)
2. **Wake lock needs a secure context.** The Screen Wake Lock API is unavailable
   at `http://192.168.42.178:5000` (LAN IP over http). A dash-mounted phone going
   to sleep guts the view. v1: the NoSleep-style silent-looping-video trick (works
   on insecure origins), engaged only while `/drive` is active. LAN HTTPS is the
   clean long-term fix, out of scope here.
3. **Breadcrumb can't come from the processed tier.** `/api/points` reads
   `track_points`, and the open moving segment lags the processor's cursor — the
   trail would visibly stop short of the van. Seed from raw `gps_points` for the
   trailing window, then extend client-side from live fixes.
4. **Annotations aren't places.** No coordinates on the row (decision 5).
5. **Map-host handoff contract.** `MapView` is a keep-alive singleton getting its
   second consumer. Drive must apply its camera/layers on enter and remove them
   on leave (and restore a sane camera for Map) — otherwise a pitched, course-up
   camera or a leftover puck leaks into the history view. The riskiest
   integration point in the plan; an explicit part of Phase 2.
6. ~~**Puck vs the existing live dot.**~~ Resolved by inspection: the Map view
   has no persistent live dot — `/api/points/latest` only feeds the one-shot
   ⊕ recenter FAB. The Drive puck is the only live marker; no conflict.

---

## Constraints carried from the project

- **Offline-first.** gpsd, the DB, and the vector basemap are all local; nothing
  here touches the WAN. The NoSleep video asset ships in the bundle.
- **GPS logging is sacred.** The live endpoint is a second gpsd *client*; the
  logger's socket and write path are untouched.
- **Committed SPA build.** New view/libs ride the normal `web/` → `static/dist/`
  build-and-commit flow; the Pi never builds.
- **Phone-first.** This view is used dash-mounted: big type, high contrast,
  touch targets, bottom-tab reachability.

---

## Architecture

```
  gpsd (localhost:2947)
     │ TPV snapshot (or cached watcher thread — open decision A)
     ▼
  GET /api/gpsd/live ──── 1 Hz poll ────▶ live store (web/src/lib/stores/live.svelte.ts)
                                            │  interpolated position @ rAF
                                            │  speed-gated heading
              ┌─────────────────────────────┼───────────────────────────┐
              ▼                             ▼                           ▼
        follow camera                puck + breadcrumb             HUD readouts
   (follow.ts: course-up,         (raw-seeded trail +         (speed / heading / alt;
    pitch, speed-zoom,             live accumulation)          OBD strip ← /api/status;
    gesture-suspend)                                           destination chevron)
```

---

## Phases

Sequenced so the follow camera — the thing that makes it a *driving* view — lands
before any HUD garnish. Each phase is independently shippable.

- ✅ **Phase 1 — Live feed** (landed 2026-07-05). `GET /api/gpsd/live` (TPV-only
  snapshot, server-computed fix age) + `stores/live.svelte.ts` (1 Hz poll, rAF
  interpolation, speed-gated heading via pure `lib/live.ts`, stale/offline state).
- ✅ **Phase 2 — `/drive` + follow camera** (landed 2026-07-05, **road tuning
  pending**). Route + 8th nav tab; `follow.ts` constants (course-up, 50° pitch,
  speed-zoom + slew); gesture-suspend (`originalEvent` on movestart distinguishes
  a real gesture from the per-frame jumpTo) + recenter pill; chevron puck (DOM
  marker, map-aligned rotation); wake lock (API where secure, bundled looping
  video elsewhere); leave-handoff restores a flat/north-up camera. Detail:
  `.claude/modules/frontend.md` § Drive view.
- ✅ **Phase 3 — HUD core + breadcrumb** (landed 2026-07-05). Bottom HUD bar (big
  mph, 16-wind heading, altitude ft) off the raw fix; breadcrumb seeded from the
  new `GET /api/points/recent` (decision 8) and live-extended (`extendCrumbs`:
  5 m movement gate, 30 min trim, count-cap decimation).
- **Phase 4 — OBD strip.** Coolant, RPM, fuel level, fuel rate while the engine is
  on; hidden/dimmed otherwise (settles C).
- **Phase 5 — Destination chevron.** Destination store (persists across reloads);
  "Navigate here" from the attraction sheet/detail + long-press dropped pin;
  HUD shows great-circle distance + chevron at (bearing-to-dest − course).
  **Forward-compat note:** `plans/trip-planner-plan.md` adds persistent
  `saved_places` (incl. free pins) — a third pin concept. Shape the destination
  store so a destination can later *be* a saved place / trip stop (don't bake
  in attraction-or-raw-coords as the only forms), and keep the long-press
  dropped pin ephemeral for now — "save this pin" belongs to the planner.

---

## Deferred pile (flagged, not scoped)

- **SSE transport** — replace the 1 Hz poll with a gpsd-bridged 5 Hz stream if
  interpolation isn't smooth enough in practice.
- **Routing engine** — Valhalla/OSRM + NA extract on the NVMe; its own plan.
- **Dark/night map style** — a dark vector style variant for night driving.
- **Road-name readout** — querying vector-tile features under the puck.
- **IMU compass** — parked/low-speed heading from `plans/motion-imu-plan.md`.
- **Annotation destinations** — resolve a time range to a representative point.

---

## Codebase touchpoints (anticipated)

- **`api/routes/`** — a live-fix endpoint (natural home: `status_gpsd.py`
  alongside `/api/gpsd/sky`, which already reads TPV+SKY live); possibly a
  raw-tier trailing-window read for the breadcrumb (Phase 3).
- **`common/gpsd.py`** — reuse/extend the snapshot helper; grows a cached-watcher
  variant if open decision A goes that way.
- **`web/src/lib/stores/`** — `live` store (poll + interpolate + heading gate);
  `destination` store (Phase 5).
- **`web/src/lib/`** — `follow.ts` (camera), puck + breadcrumb rendering (extend
  `map.ts` or a sibling), wake-lock helper.
- **`web/src/views/`** — `Drive.svelte` + HUD components; "Navigate here" entry
  points in `AttractionSheet`/`AttractionDetail`.
- **`web/src/lib/routes.ts` / `Shell.svelte`** — the `/drive` destination.
- **`api/app.py`** — nothing beyond the SPA catch-all already covering `/drive`.
- **Tests** — endpoint read path (Flask client), heading-gate + interpolation
  logic (pure, if factored TS-side keep it Vitest-able), chevron bearing math.
