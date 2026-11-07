# Map chrome plan — shell-owned controls, Drive theme, compass readability, sidebar collapse

Status: **in flight** (shaped 2026-11-07). Fold into `frontend.md` when done, then drop.

## Why

The map engine, the `layers` store and the `selection` store are all singletons
("global state"), but their controls were rendered by whichever view happened to
own them ("local chrome"): the only theme control lived in Map's rail, so Drive
could not switch light/dark; the label hook was installed from `Map.svelte` only,
so a fresh load landing on `/drive` never applied theme-dependent label settings.
Rule going forward: **engine-wide state → shell-owned chrome; view-local state →
view chrome.**

## Decisions

- **M1 — Route-declared map chrome.** `MAP_CHROME` in `routes.ts` (keyed by route
  path, like `SECTIONS`) lists the rail panels a map route wants; `Shell` renders
  `MapChrome.svelte` from it. Map: style + data + inspect (+ the overlay dock).
  Drive: style only — a **single button** (user's call: a column would cover the
  compass ring along the top-right edge). Weather: style only. Views keep their
  own chrome (Map: annotations/FAB/legends; Drive: status/recenter/compass/HUD;
  Weather: the radar panel).
- **M2 — The label hook installs from the shell chrome**, not Map, so it is live on
  every map route from first load.
- **M3 — The TimeDock is one shared component; placement is per layout.** Map's
  bottom-overlay dock is map chrome (`timeDock: true` in `MAP_CHROME`); Trends
  renders the same component in-flow above its chart (picker opening down). Weather's radar scrubber is a *different* axis (7-min
  frames, loop) and stays per-view by design. Drive is live-only.
- **M4 — No app-wide light mode.** "Theme" means the basemap vector theme; the
  chrome stays dark glass over either ground.
- **M5 — Ground-aware overlays via a `data-map-ground` attribute** (`light|dark`),
  derived from the theme (`DARK_THEMES` in `labels.ts`; USGS raster = light), so
  CSS flips the compass halo without JS per mark.

## Items

1. [x] Route-declared map chrome (M1 + M2): `MAP_CHROME` registry, `MapChrome.svelte`,
   Shell renders it, Map.svelte sheds the rail.
2. [x] Persist map style prefs (theme/base/terrain/exaggeration/minor roads) in
   localStorage, versioned key (`lib/mapPrefs.ts`; engine + store both seed from
   it). Data-layer toggles stay session-scoped (flagged — user to confirm).
3. [ ] Drive quick toggle: one-tap ☀/🌙 in the HUD flipping light ↔ dark.
4. [ ] Ground-aware compass styling (M5): dark text + light halo on light ground.
5. [ ] Compass text size up (names 13→16, cities 14→18, meta 10→12 first guess);
   the placement size estimate in `DriveCompass.svelte` derives from the same
   constants so marks can't overlap.
6. [ ] Collapsible desktop sidebar (icon-only ~56px, persisted; fault block gets an
   icon-only form; verify MapLibre picks up the container resize).
7. [ ] Fold into `frontend.md` (Shell/Map/Drive sections), drop this file.

## Later / flagged

- "Auto" theme from solar elevation (position + time; no dependency).
- Auto-collapse the sidebar on Drive?
