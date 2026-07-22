# Desktop-first redesign plan

Make the SPA read as a real desktop app on a parked laptop, not a phone layout
stretched wide. The nav shell already goes sidebar-on-desktop; the **content** has
no desktop layout — every view is the mobile single column filling `main`
(1380px wide, no `max-width`). This plan gives the content a desktop story:
capped width, multi-column dashboards, dense tables, and a small IA cleanup —
while the **phone stays the primary, first-class client** (every item *adds* a
desktop layout, never replaces the phone one).

## Locked decisions (2026-07-22 shaping session)

- **Scope:** desktop-first redesign *including* IA — kill hub indirection, adopt
  master-detail where a list has a detail, add a Diagnostics tab.
- **Target device:** a parked laptop (~1440px). Cap control/prose content at
  `--content-max` (~1140px) for readability; **dense tables may fill** the wider
  available width (a table wants the room).
- **Systems hub → live dashboard.** The Overview landing stops being a menu of
  launcher tiles and becomes a glanceable dashboard of live panels. Same for the
  new Diagnostics overview.
- **Nav split:** new top-level **Diagnostics** (🩺) holds the service/infra health
  readouts; **Systems** keeps the real van subsystems.
  - Systems = **Sensors, Fridge, Trends** (Trends stays cross-listed as its own tab).
  - Diagnostics = **Time** (ntp), **GPS** (gpsd), **Logs** (syslog), **Media**
    (mediamtx), **Offline data**.
  - Mechanics: one `NAV` entry, split `SECTIONS['/systems']` in two, retag five
    routes' `tab` in `routes.ts`.
- **Master-detail** for **Radio** (log → player/detail) and **Sky** (passes →
  skyplot); other lists become dense single-column, not master-detail.
- **Phone nav:** 11 tabs overflows the bottom bar (already tight at 10, labels
  ellipsize ≤390px). Add a **"More" overflow** for lower-traffic destinations.
  Otherwise nav is unchanged (10→11 tabs, no regrouping beyond the split).

## Baseline (measured 2026-07-22, playwright @ 1600×1000, live van)

| View | Symptom | Metric |
|------|---------|--------|
| Radio | full-width controls stacked single-column; slider stretched ~1300px | **3.87 screens** tall |
| Sky/Passes | 114 passes each a full-width 3-subcard row | **16.4 screens** tall |
| Systems | hub is a menu redundant with the pill nav | fills ~35% of one screen |

Re-measure the same three after conversion; target Radio ≤1.5 screens, Passes a
dense table (~35 rows/screen), Systems a full dashboard.

## Phases (each phase is independently shippable)

### Phase 0 — Layout foundation (the leverage)
- [x] **Breakpoint story.** Resolved to **pure CSS** — media queries for the shell
      (existing 768px) + **container queries** for per-view column layout (keys off
      the page's own width, immune to the sidebar). No JS viewport store needed:
      Places proves one-pane-vs-two works in CSS + a `detailOpen` flag. Add the rune
      only if a real control-flow need appears.
- [x] **Tokens** (`app.css`): `--content-max: 1140px`. Reused the existing `.card`
      rather than adding a denser `.panel` — revisit if a density pass needs it.
- [x] **`.app-page`** utility (a class, not a component): caps + centers content and
      is a **container-query root**. `.app-page--wide` = fill (fill-vs-cap rule).
- [x] **`.dash-grid`** utility: auto-fit multi-column → 1-col, `--dash-min` tunes
      column count. Added; first users are the Systems/Home dashboards (not yet wired).
- [ ] **`DataTable` / `DenseList`**: sortable, sticky header, compact rows;
      collapses to stacked cards on phone. **Deferred to Sky/Passes** (first real
      table). Radio's log used a bespoke master-detail instead.
- [~] **`MasterDetail`**: implemented **inline in Radio** (CSS-grid list|detail via
      container query, bounded-height list). Extract to a shared shell when Sky needs
      it (and reconcile with the Places pattern).

### Phase 1 — IA / navigation
- [x] Added **Diagnostics** tab (🩺); split `SECTIONS`; retagged `ntp`/`gpsd`/
      `syslog`/`mediamtx`/`data` → `tab: '/diagnostics'`; new `/diagnostics` route +
      `Diagnostics.svelte`. Systems now = Sensors/Trends/Fridge; Diagnostics =
      Time/GPS/Logs/Media/Data.
- [x] Phone **"More" overflow** — `PHONE_PRIMARY_TABS` (Home/Map/Drive/Places/Radio)
      stay on the bar (65px each, no clipping); the other six fold into a 2-col More
      sheet. Desktop sidebar shows all 11 (More hidden). CSS-only reveal.
- [x] Systems Overview → **live subsystem dashboard** (House power SOC/V/solar/load,
      Cabin temp/humidity/IAQ, Fridge — from `/api/status` + `/api/sensors`) over the
      launcher tiles. Diagnostics Overview → **health board** (5 status tiles).
  - [ ] *Polish:* extract a shared `StatPanel` — Systems' `.panel/.stat` recipe
        mirrors Home's. Diagnostics could gain richer glances (offset ms, buffer).

### Phase 2 — Worst offenders (prove the primitives)
- [x] **Radio** → readout (full-width) / **operate** column (Listen · Transmit)
      beside a **configure** two-column masonry (Tune/Levels/CTCSS/Repeater/
      Cross-band/Power) / full-width **master-detail** log inbox (scrollable list +
      sticky player-detail pane). **3.87 → 1.83 screens** @1440; phone unchanged;
      163 tests + svelte-check clean; verified on the live van at 1440 + 390.
- [x] **Sky/Passes** → dense **sortable** table (Sat/System/When/Rise/Peak/Set/Dur/
      Signal; 5 sortable cols, sticky header) on desktop; the cards stay on phone,
      toggled by container query. Lands on content already (Passes is the default).
      **16.4 → 4.11 screens** @1440; sort verified (Peak → 90°/88°/88°); phone cards
      intact; svelte-check clean; verified on live van at 1440 + 390.
  - [ ] *Follow-on (optional):* master-detail passes **|** skyplot — click a row →
        its arc on an embedded skyplot. More feature than density fix; the table
        already solved the offender. Decide whether it's worth the canvas wiring.
- [ ] **Systems + Diagnostics** live dashboards (the overview components from 1.3).

### Phase 3 — Sweep the rest
Apply `AppPage` / `DashboardGrid` / `DataTable` + a density pass to: Home,
Sensors, Trends, Fridge, Docs, Places (density only — already master-detail),
the StatusCheckPage drill-ins (Data/Time/GPS/Logs/Media), Cameras.

### Phase 4 — Verify + ship
- [ ] playwright-cli at **~1440 AND ~390** for every touched view (phone stays
      first-class), **0 console errors**.
- [ ] Build + commit `static/dist/` before push (the Pi never builds).
- [ ] `git push all main`.

## Conventions for this pass
- **Worst-first**, and land each phase deployable — a converted Radio and an
  unconverted Docs coexist fine.
- Every view item = "add a desktop layout," never "replace the phone one."
- Fill-vs-cap: control/prose panels cap at `--content-max`; dense tables fill.
- Verify each converted view on-device (van over the HaLow bridge) at both widths.

## Open / deferred
- Whether Diagnostics/Systems overviews need any *new* API reads (prefer to derive
  from the already-polled aggregates).
- Nav regrouping beyond the Diagnostics split — out of scope this pass.
