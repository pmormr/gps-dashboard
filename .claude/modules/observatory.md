# GNSS Observatory

Long-term satellite logging, a 3D constellation globe, and pass prediction —
beyond the live `/skyplot`. Same capture → derive → serve → render split as the
rest of the app: the logger captures per-SV az/el, `common/` does the orbital
geometry, the API serves it on-demand, and three views render it.

## Capture

The logger writes a `sat_observations(timestamp, gnssid, svid, az, el, snr, used,
health)` row per positioned satellite from each SKY message, on its own ~60s
throttle (separate from the 5s `receiver_metadata` throttle; both gate off SKY,
off the position hot-path). `timestamp` is canonical ms-UTC like every tier.
~50k rows/day. The heartbeat gains a `satobs=` counter.

## Reconstruction — `common/satgeo.py`

The log records *direction* to each satellite (az/el), never range — a direction
can't place a point in space. But GNSS sats orbit at known nominal altitudes, so
the position is recoverable: from the observer's ECEF position, shoot the az/el
ray outward and intersect it with the sphere of that constellation's orbital
radius (`ORBITAL_RADIUS_M` per gnssid). Display-grade only — nominal *circular*
radius, so BeiDou/QZSS mixed orbit classes are coarse. `ecef_to_azel` is the
inverse (used by prediction); `gmst_rad` + `ecef_to_eci`/`eci_to_ecef` provide
the inertial frame the orbit fit needs.

## Orbit fit + prediction — `common/orbits.py`

**Key idea:** fit and propagate in the **inertial (ECI) frame**, and
"sidereal-repeat" falls out for free — the orbit is fixed in ECI, the parked
observer rotates with Earth (GMST), so they re-align each sidereal day, and it
generalizes to constellations whose ground tracks *don't* repeat daily, over any
horizon. The priors are strong, so it's a 1-DOF fit per SV:

- **Size**: nominal radius → mean motion `n` (not independently observable, since
  reconstruction *assumed* the radius).
- **Plane**: `fit_orbit_normal` applied to the **ECI** track (fitting in ECEF
  would smear it by Earth rotation — that's why the `/globe` display rings, fit
  in ECEF, are only display-grade).
- **Phase**: the lone free parameter — one epoch angle, circular-mean fit.

`fit_orbit` gates on observed-vs-nominal angular rate, rejecting SVs in the wrong
orbit class for their gnssid (BeiDou GEO/IGSO among MEO, eccentric QZSS).
`find_passes` coarse-scans propagated elevation, then bisects rise/set and
ternary-searches the peak. Two-body only — no J2 nodal precession (a hook for
later; accurate to minutes over a 1–2 day horizon, the prediction window).

## API — `api/observatory.py` (shared) + routes

`api/observatory.py` holds the anchor-fix + az/el→ECEF reconstruction shared by
both reads (one observer fix anchors a window; parked van barely moves vs the
26,000 km baseline).

- `GET /api/constellation?start=&end=` (`api/routes/globe.py`) — reconstructed 3D
  `samples` over a window, grouped by SV, **plus each SV's inertial `orbit` fit
  params** (`epoch, radius_km, n, phase0, u, v, normal`; null when unfittable).
  The client propagates those params itself (the ECI/GMST propagator is ported to
  `web/src/lib/globe.ts`) so the dot, trail, ring, and focused full-period orbit all come from
  **one fit through one model** — there is no second, ECEF-smeared `fit_orbit_normal`
  ring anymore, which is what used to leave dots off their rings. Feeds `/globe`.
- `GET /api/passes?hours=&mask=&track=1` (`api/routes/passes.py`) — fits each SV
  seen in the trailing 72h, propagates over the horizon (≤48h), returns upcoming
  passes (RINEX name, rise/peak/set times+az, peak el, duration, in-progress, max
  SNR) sorted by rise. `mask` (≤30, **0 is valid** = horizon) is the rise/set
  elevation; `track=1` attaches a 32-pt `[az,el]` polyline per pass for the
  skyplot overlay. Unfittable SVs are counted, not failed.

## Render

- `/globe` (`Globe.svelte` + `web/src/lib/globe.ts`) — three.js textured
  Earth in ECEF (npm three 0.160.1, dynamic-imported; Earth textures in
  `static/img/`, offline). The client propagates the server's `orbit` params for
  each SV: a dot at the current position (set SVs on the far side) on a faint
  **instantaneous ring** (the inertial plane rotated into ECEF at the window end,
  so the dot sits exactly on its ring). Click-to-focus highlights the SV with
  three bold pulsing `Line2` fat lines: a **white instantaneous great-circle ring**
  (the idealized orbit) plus the constellation-coloured **full-period propagated
  path** and **recent trail** — the true ECEF motion, which precesses off the ring
  as Earth rotates (the dot + trail are sub-segments of the path). **Trails are
  focus-only by default** (the Trails toggle shows all at once), since each
  precessing path is unreadable in bulk. Observer marker, sight-lines
  (above-horizon only), click-popup. **PC-only** (WebGL).
  `?demo` synthesises a constellation offline.
- `/passes` (alias of `/sky` — `Sky.svelte`) — phone-friendly
  schedule, one card per pass (rise/peak/set + compass azimuths, peak el, live
  countdown), constellation-coloured. Horizon/mask chips, 60s auto-refresh.
- `/skyplot` (`Skyplot.svelte` + `web/src/lib/skyplot.ts`) **Predicted** toggle — overlays each upcoming pass as a dashed
  constellation-coloured az/el arc on the dome (in-progress = forward path to
  set; future = hollow rise marker + name). Honours legend toggles, slow refresh,
  deep-link `?passes`. Fetches `/api/passes?...&track=1`.

## Validation — two backtests

**Self-consistency — `tools/passes_validate.py`** (offline). The receiver's own
later sightings are the only *offline* ground truth: fit each SV on the earlier
part of its log and predict az/el at every held-out later observation. **Real Pi
data: median 0.50°, p90 1.10°** (69 SVs, 3466 sightings) — SBAS 0.04°
(geostationary), Galileo/BeiDou/GLONASS ~0.45–0.49°, GPS 0.75° (weakest,
nominal-radius approx). Run with `--db /mnt/nvme/data/gps_history.db`.

**Absolute — `tools/tle_validate.py`** (dev-time, needs internet). Cross-checks
against CelesTrak GNSS TLEs propagated by **SGP4** (the upstream Spacebook
re-serves; `sgp4` is a dev dep). SGP4 emits TEME, the same GMST-rotated frame our
own propagation uses, so the truth path reuses `gmst_rad`/`eci_to_ecef`/
`ecef_to_azel`. Satellites are matched **geometrically** — nearest-in-sky per
constellation, modal vote locks the NORAD id — so no PRN/slot table to rot. Three
numbers vs TLE truth: **input geometry** (logged az/el, the receiver's own
accuracy) **1.2° median**; **orbit-model prediction** (our fit → held-out)
**1.5° median**; **reconstruction 3D** (nominal-radius vs truth) ~500 km MEO, with
SBAS/BeiDou-GEO/IGSO tails from the radius approximation. This catches what
self-consistency structurally can't — a GMST-sign error cancels in the round-trip
but not against TLEs, and a synthetic/demo DB reads a perfect 0.00°
self-consistent yet explodes here. **The 1.5° floor is not data-starved:** it
plateaus after ~2 h of arc, and a >16 h fit *worsens* (two-body can't track J2
nodal precession); the floor is gpsd's ~1.2° az/el + the nominal radius + no-J2.
Needs internet but caches a TLE snapshot for offline reuse; run against a DB
snapshot (`.backup` the live WAL DB, pull, `--db`). Tested offline in
`tests/test_tle_validate.py` (vendored TLEs → synthesized track → recovery).

**Metadata — `tools/fetch_satcat.py` + `common/satcat.py`.** Caches CelesTrak
SATCAT (owner-country, launch, orbit geometry, RCS size, object type, status;
block/generation is in the object name) keyed by NORAD id, joined to each fit via
`Satrec.satnum`. No bus manufacturer ("make") — not in SATCAT. Surfaced in
`tle_validate -v` (e.g. `G13 … GPS BIII-10 [US 2026]`).

## Decisions / traps

- **Orbit source: fit from observed az/el (route A), not broadcast subframes
  (route B).** gpsd 3.22 emits no `SUBFRAME` by default; route B would mean
  reconfiguring the production M9N to stream UBX-RXM-SFRBX (the fragile config
  surface that once stalled logging) for orbital elements whose az/el is already
  in every SKY message. Route A is zero-hardware-risk and fully offline.
- **Parked-observer assumption** — predictions/reconstruction anchor to one fix;
  valid while parked (the sky is location-relative). The user reads the window
  knowing when parked.
- On-the-fly reconstruction/fitting, **no rollup table or analyzer service yet**
  — revisit if scan time bites.
- Possible upgrades if ever needed: J2 nodal precession (longer horizons), better
  GPS radius, BeiDou orbit-class detection; globe polish (time-fade arcs,
  real-sun day/night, location/dwell auto-scoping).
