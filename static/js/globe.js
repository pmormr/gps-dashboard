/**
 * 3D constellation globe.
 *
 * Renders the satellites we've logged (`/api/constellation`) in true 3D scale
 * around a textured Earth, in the same Earth-centred Earth-fixed (ECEF) frame the
 * server reconstructs positions in: +X through (lat 0, lon 0), +Y through
 * (lat 0, lon 90E), +Z through the north pole. Working directly in ECEF keeps the
 * observer marker and every satellite physically consistent — only the Earth
 * texture has to be aligned to that frame.
 *
 * Per satellite: a dot at its current predicted position — propagated from the
 * fitted orbit, so a satellite that has set sits on the far side of the Earth
 * instead of frozen at the horizon — plus a faint full orbit ring, coloured by
 * constellation. Clicking a satellite focuses it: its orbit ring brightens and
 * its predicted trail (the true ground-relative path up to the dot) is drawn.
 * Trails are per-focus by default because each bends away from its great-circle
 * ring as Earth rotates and many at once is unreadable; the Trails toggle shows
 * them all. (Without an orbit fit a dot falls back to the last observed sample,
 * and the all-trails view to the observed track split at between-pass gaps so it
 * never chords across the planet.) A marker pins the van's location, with
 * sight-lines to the satellites currently above the horizon. PC browsers only
 * (WebGL, mouse orbit/zoom). `?demo` synthesises a constellation for offline
 * development.
 */

import * as THREE from 'three';
import { OrbitControls } from '/static/vendor/three/OrbitControls.js';

const DEMO = new URLSearchParams(location.search).has('demo');
const DEG = Math.PI / 180;

/** gpsd gnssid → display name + colour (matches the skyplot palette). */
const GNSS = {
  0: { name: 'GPS', color: 0x22c55e },
  1: { name: 'SBAS', color: 0x94a3b8 },
  2: { name: 'Galileo', color: 0xf59e0b },
  3: { name: 'BeiDou', color: 0xef4444 },
  5: { name: 'QZSS', color: 0xa78bfa },
  6: { name: 'GLONASS', color: 0x3b82f6 },
};

/**
 * Longitude alignment of the equirectangular Earth texture in the ECEF frame.
 * With `rotation.x = π/2`, the texture's prime meridian (u=0.5) already lands on
 * ECEF +X and the north pole on +Z, so no longitude shift is needed.
 */
const TEXTURE_OFFSET = 0.0;

const GM_KM3_S2 = 398600.4418; // Earth's gravitational parameter, km^3/s^2
const PICK_PX = 18; // click pick tolerance, screen pixels

/** gnssid → RINEX constellation letter, for satellite names (G01, E11, …). */
const GNSS_LETTER = { 0: 'G', 1: 'S', 2: 'E', 3: 'C', 5: 'J', 6: 'R' };

const container = document.getElementById('globe-container');
const statusEl = document.getElementById('status');
const infoEl = document.getElementById('info');
const legendEl = document.getElementById('legend');
const popupEl = document.getElementById('sat-popup');

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight);
container.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x05080f);

const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 10, 2_000_000);
camera.up.set(0, 0, 1); // ECEF north is +Z
camera.position.set(80000, -78000, 52000);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.minDistance = 8000;
controls.maxDistance = 600000;
controls.autoRotateSpeed = 0.35;

scene.add(new THREE.AmbientLight(0xffffff, 0.35));
const sun = new THREE.DirectionalLight(0xffffff, 1.5);
sun.position.set(1, 0.5, 0.55).multiplyScalar(1e6);
scene.add(sun);

// Starfield backdrop for depth/scale.
scene.add(makeStars());

let earth = null;
const satGroup = new THREE.Group();
const markerGroup = new THREE.Group();
const highlightGroup = new THREE.Group();
scene.add(satGroup);
scene.add(markerGroup);
scene.add(highlightGroup);

/** Per-satellite pick targets {sat, pos}, rebuilt each render for click picking. */
const picks = [];
/** Observer ECEF (km), kept for the popup's sky-angle math. */
let observerVec = null;

/** UI state: window hours, layer toggles, and per-constellation visibility. */
const state = {
  hours: 24,
  trails: false,
  rays: true,
  orbits: true,
  spin: true,
  hidden: new Set(),
};

/**
 * Build the textured Earth at the given radius (km) and add it to the scene.
 *
 * The sphere's poles are rotated onto ECEF +Z, and the colour map's longitude is
 * shifted so painted continents line up with the ECEF frame (so the van marker,
 * placed by true ECEF, sits over the right place).
 *
 * @param {number} radiusKm Earth render radius in km.
 */
function buildEarth(radiusKm) {
  if (earth) {
    scene.remove(earth);
    earth.geometry.dispose();
  }
  const loader = new THREE.TextureLoader();
  const map = loader.load('/static/img/earth_atmos_2048.jpg');
  map.colorSpace = THREE.SRGBColorSpace;
  map.wrapS = THREE.RepeatWrapping;
  map.offset.x = TEXTURE_OFFSET;
  const material = new THREE.MeshPhongMaterial({
    map,
    specularMap: loader.load('/static/img/earth_specular_2048.jpg'),
    normalMap: loader.load('/static/img/earth_normal_2048.jpg'),
    shininess: 14,
    specular: 0x224466,
  });
  earth = new THREE.Mesh(new THREE.SphereGeometry(radiusKm, 96, 64), material);
  earth.rotation.x = Math.PI / 2; // align sphere poles to ECEF +Z
  scene.add(earth);
}

/** @returns {THREE.Points} A sparse starfield far outside the constellation. */
function makeStars() {
  const n = 1500;
  const pos = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const v = new THREE.Vector3().randomDirection().multiplyScalar(900000);
    pos.set([v.x, v.y, v.z], i * 3);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  return new THREE.Points(geo, new THREE.PointsMaterial({ color: 0x9fb3d0, size: 1200, sizeAttenuation: true }));
}

/** Remove and dispose every object in a group. */
function clearGroup(group) {
  for (const child of [...group.children]) {
    group.remove(child);
    if (child.geometry) child.geometry.dispose();
    if (child.material) child.material.dispose();
  }
}

/**
 * A constellation-coloured arc line through ECEF points (km).
 *
 * @param {THREE.Vector3[]} points Ordered positions.
 * @param {number} color Constellation colour.
 * @returns {THREE.Line} The arc line.
 */
function arcLine(points, color) {
  const geo = new THREE.BufferGeometry().setFromPoints(points);
  return new THREE.Line(geo, new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.7 }));
}

/**
 * Split an observed track at between-pass gaps.
 *
 * Consecutive observations within a pass are ~60 s apart (a tiny angular step);
 * a multi-hour below-horizon gap shows up as a large jump between the set point
 * and the next rise. Breaking the polyline there stops the renderer drawing a
 * straight chord across the planet between two passes. Used only as the no-fit
 * fallback (a fitted satellite gets a smooth predicted trail instead).
 *
 * @param {THREE.Vector3[]} pts Time-ordered ECEF positions (km).
 * @param {number} maxStepDeg Angular jump (deg) that starts a new segment.
 * @returns {THREE.Vector3[][]} Contiguous segments.
 */
function splitAtGaps(pts, maxStepDeg = 30) {
  const minCos = Math.cos(maxStepDeg * DEG);
  const segs = [];
  let seg = [];
  for (const p of pts) {
    if (seg.length) {
      const cos = seg[seg.length - 1].clone().normalize().dot(p.clone().normalize());
      if (cos < minCos) {
        segs.push(seg);
        seg = [];
      }
    }
    seg.push(p);
  }
  if (seg.length) segs.push(seg);
  return segs;
}

/**
 * Whether a position is above the observer's local horizon.
 *
 * @param {THREE.Vector3} obsVec Observer ECEF (km).
 * @param {THREE.Vector3} pos Target ECEF (km).
 * @returns {boolean} True when the line of sight points above the horizon.
 */
function aboveHorizon(obsVec, pos) {
  return pos.clone().sub(obsVec).dot(obsVec.clone().normalize()) > 0;
}

/**
 * Rebuild the satellite arcs, dots, and sight-lines from a constellation payload.
 *
 * @param {Object} data `/api/constellation` response (observer + per-SV samples).
 */
function render(data) {
  buildEarthIfNeeded(data.earth_radius_km);
  clearGroup(satGroup);
  clearGroup(markerGroup);
  picks.length = 0;
  hidePopup();

  const obs = data.observer;
  const obsVec = new THREE.Vector3(obs.x, obs.y, obs.z);
  observerVec = obsVec;
  addMarker(obsVec);

  const seen = new Set();
  let sampleCount = 0;
  for (const sat of data.sats) {
    const meta = GNSS[sat.gnssid] || { name: 'Other', color: 0x64748b };
    seen.add(sat.gnssid);
    sampleCount += sat.samples.length;
    const pts = sat.samples.map((s) => new THREE.Vector3(s.x, s.y, s.z));
    const trail = (sat.trail || []).map((p) => new THREE.Vector3(p.x, p.y, p.z));
    // Dot at the current predicted position (a set SV is on the far side); fall
    // back to the last observed sample when the orbit could not be fit.
    const last = sat.predicted
      ? new THREE.Vector3(sat.predicted.x, sat.predicted.y, sat.predicted.z)
      : pts[pts.length - 1];

    picks.push({ sat, pos: last });

    const obj = new THREE.Group();
    obj.userData.gnssid = sat.gnssid;
    obj.visible = !state.hidden.has(sat.gnssid);

    if (state.orbits && sat.orbit) {
      obj.add(orbitRing(sat.orbit, meta.color));
    }

    // The focused satellite always gets its trail (in showPopup); the Trails
    // toggle additionally draws every satellite's trail at once — informative but
    // busy (each is the true ground-relative path, which bends away from the
    // great-circle ring as Earth rotates), so it defaults off.
    if (state.trails) {
      // Prefer the predicted trail (wraps around the back to the dot); without a
      // fit, draw the observed track split at between-pass gaps so the line never
      // chords across the planet.
      if (trail.length >= 2) {
        obj.add(arcLine(trail, meta.color));
      } else {
        for (const seg of splitAtGaps(pts)) {
          if (seg.length >= 2) obj.add(arcLine(seg, meta.color));
        }
      }
    }

    const snr = sat.samples[sat.samples.length - 1].snr || 0;
    const dot = new THREE.Mesh(
      new THREE.SphereGeometry(180 + Math.min(50, snr) * 9, 12, 10),
      new THREE.MeshBasicMaterial({ color: meta.color }),
    );
    dot.position.copy(last);
    obj.add(dot);

    // Sight-line only when the satellite is actually above the horizon now; a
    // set SV's predicted dot is behind the Earth, so no ray is drawn to it.
    if (state.rays && aboveHorizon(obsVec, last)) {
      const geo = new THREE.BufferGeometry().setFromPoints([obsVec, last]);
      obj.add(new THREE.Line(geo, new THREE.LineBasicMaterial({
        color: meta.color, transparent: true, opacity: 0.18,
      })));
    }
    satGroup.add(obj);
  }

  renderLegend(seen);
  renderInfo(data, sampleCount);
  setStatus('');
}

let earthRadiusKm = 0;
/** Build the Earth once, at the radius the server reports. */
function buildEarthIfNeeded(radiusKm) {
  if (radiusKm && radiusKm !== earthRadiusKm) {
    earthRadiusKm = radiusKm;
    buildEarth(radiusKm);
  }
}

/**
 * Build the full great-circle orbit ring from a fitted plane normal.
 *
 * The orbital plane passes through Earth's centre, so the complete orbit is the
 * circle of the constellation's orbital radius lying in that plane — drawn even
 * where we never observed the satellite (the far side, below the horizon).
 *
 * @param {Object} orbit `{nx, ny, nz, radius_km}` from the API.
 * @param {number} color Constellation colour.
 * @param {number} opacity Line opacity (faint for the overview, bright on focus).
 * @returns {THREE.LineLoop} The ring loop.
 */
function orbitRing(orbit, color, opacity = 0.22) {
  const n = new THREE.Vector3(orbit.nx, orbit.ny, orbit.nz).normalize();
  const ref = Math.abs(n.z) < 0.9 ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(1, 0, 0);
  const u = new THREE.Vector3().crossVectors(ref, n).normalize();
  const v = new THREE.Vector3().crossVectors(n, u);
  const pts = [];
  const segments = 160;
  for (let i = 0; i < segments; i++) {
    const t = (i / segments) * 2 * Math.PI;
    pts.push(new THREE.Vector3()
      .addScaledVector(u, orbit.radius_km * Math.cos(t))
      .addScaledVector(v, orbit.radius_km * Math.sin(t)));
  }
  const geo = new THREE.BufferGeometry().setFromPoints(pts);
  return new THREE.LineLoop(geo, new THREE.LineBasicMaterial({ color, transparent: true, opacity }));
}

/**
 * Add the observer marker: a bright dot on the surface plus a short radial spike.
 *
 * @param {THREE.Vector3} obsVec Observer ECEF position (km).
 */
function addMarker(obsVec) {
  const dot = new THREE.Mesh(
    new THREE.SphereGeometry(160, 16, 12),
    new THREE.MeshBasicMaterial({ color: 0xffffff }),
  );
  dot.position.copy(obsVec);
  markerGroup.add(dot);

  const outward = obsVec.clone().normalize().multiplyScalar(obsVec.length() + 2400);
  const geo = new THREE.BufferGeometry().setFromPoints([obsVec, outward]);
  markerGroup.add(new THREE.Line(geo, new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.7 })));
}

/** Rebuild the constellation legend chips for the constellations present. */
function renderLegend(seen) {
  const ids = [...seen].sort((a, b) => a - b);
  legendEl.innerHTML = ids.map((id) => {
    const meta = GNSS[id] || { name: 'Other', color: 0x64748b };
    const hex = '#' + meta.color.toString(16).padStart(6, '0');
    const off = state.hidden.has(id) ? ' off' : '';
    return `<button class="leg${off}" data-gnss="${id}"><span class="sw" style="background:${hex}"></span>${meta.name}</button>`;
  }).join('');
  legendEl.querySelectorAll('.leg').forEach((b) => {
    b.addEventListener('click', () => {
      const id = Number(b.dataset.gnss);
      state.hidden.has(id) ? state.hidden.delete(id) : state.hidden.add(id);
      applyVisibility();
      renderLegend(seen);
    });
  });
}

/** Apply per-constellation visibility toggles to the satellite groups. */
function applyVisibility() {
  for (const obj of satGroup.children) {
    obj.visible = !state.hidden.has(obj.userData.gnssid);
  }
}

/** Update the info readout (observer, counts, window, anchor time). */
function renderInfo(data, sampleCount) {
  const o = data.observer;
  const lat = o.lat.toFixed(4);
  const lon = o.lon.toFixed(4);
  const when = o.timestamp ? new Date(o.timestamp).toLocaleString() : '—';
  infoEl.innerHTML =
    `Observer <b>${lat}°, ${lon}°</b><br>` +
    `Satellites <b>${data.sats.length}</b> · samples <b>${sampleCount.toLocaleString()}</b><br>` +
    `Window <b>${state.hours}h</b> · anchored <b>${when}</b>`;
}

/** Show or clear the centered status overlay. */
function setStatus(msg) {
  statusEl.textContent = msg;
  statusEl.classList.toggle('hide', !msg);
}

/**
 * Find the front-most satellite within the pick tolerance of a screen point.
 *
 * @param {number} px Click X in CSS pixels.
 * @param {number} py Click Y in CSS pixels.
 * @returns {{sat:Object, pos:THREE.Vector3, sx:number, sy:number}|null} Hit or null.
 */
function pickAt(px, py) {
  let best = null;
  let bestCamDist = Infinity;
  for (const p of picks) {
    if (state.hidden.has(p.sat.gnssid)) continue;
    const ndc = p.pos.clone().project(camera);
    if (ndc.z > 1) continue; // behind the camera
    const sx = (ndc.x * 0.5 + 0.5) * window.innerWidth;
    const sy = (-ndc.y * 0.5 + 0.5) * window.innerHeight;
    if (Math.hypot(sx - px, sy - py) > PICK_PX) continue;
    const camDist = p.pos.distanceTo(camera.position);
    if (camDist < bestCamDist) {
      bestCamDist = camDist;
      best = { sat: p.sat, pos: p.pos, sx, sy };
    }
  }
  return best;
}

/** RINEX-style satellite name, e.g. G01 / E11. */
function satName(gnssid, svid) {
  return (GNSS_LETTER[gnssid] || '?') + String(svid).padStart(2, '0');
}

/** Circular-orbit speed (km/s) at a geocentric radius (km). */
function orbitalSpeedKms(radiusKm) {
  return Math.sqrt(GM_KM3_S2 / radiusKm);
}

/**
 * Elevation/azimuth of a satellite as seen from the observer.
 *
 * @param {THREE.Vector3} pos Satellite ECEF position (km).
 * @returns {{el:number, az:number}|null} Degrees, or null without an observer.
 */
function skyAngles(pos) {
  if (!observerVec) return null;
  const up = observerVec.clone().normalize();
  const los = pos.clone().sub(observerVec).normalize();
  const el = Math.asin(THREE.MathUtils.clamp(up.dot(los), -1, 1)) / DEG;
  const east = new THREE.Vector3().crossVectors(new THREE.Vector3(0, 0, 1), up);
  if (east.lengthSq() < 1e-9) east.set(1, 0, 0);
  east.normalize();
  const north = new THREE.Vector3().crossVectors(up, east);
  let az = Math.atan2(los.dot(east), los.dot(north)) / DEG;
  if (az < 0) az += 360;
  return { el, az };
}

/**
 * Show the info popup for a satellite, and highlight its orbit + recent trail.
 *
 * Focusing a satellite reveals detail the cluttered overview hides: its full
 * orbit ring brightened so the plane stands out, and its predicted trail (the
 * true ground-relative path up to the current dot, drawn one at a time so the
 * Earth-rotation bend reads in context rather than as noise) — both in the
 * highlight group, cleared on unfocus.
 *
 * @param {Object} sat The API satellite object.
 * @param {THREE.Vector3} pos Its current ECEF position (km).
 * @param {number} sx Screen X to anchor the popup.
 * @param {number} sy Screen Y to anchor the popup.
 */
function showPopup(sat, pos, sx, sy) {
  clearGroup(highlightGroup);
  const meta = GNSS[sat.gnssid] || { name: 'Other', color: 0x64748b };
  const hex = '#' + meta.color.toString(16).padStart(6, '0');
  const radiusKm = pos.length();
  const altKm = radiusKm - earthRadiusKm;
  const latest = sat.samples[sat.samples.length - 1];
  const sky = skyAngles(pos);
  const lastSeen = latest.t ? new Date(latest.t).toLocaleTimeString() : '—';
  const signal = (typeof latest.snr === 'number' && latest.snr > 0)
    ? `${latest.snr.toFixed(0)} dB-Hz${latest.used ? ' · used' : ''}`
    : '—';
  popupEl.innerHTML =
    '<div class="pop-head">' +
      `<div class="pop-name"><span class="sw" style="background:${hex}"></span>${satName(sat.gnssid, sat.svid)}</div>` +
      '<button class="pop-close" aria-label="Close">×</button>' +
    '</div>' +
    `<div class="pop-row"><span>System</span><b>${meta.name}</b></div>` +
    `<div class="pop-row"><span>Altitude</span><b>${Math.round(altKm).toLocaleString()} km</b></div>` +
    `<div class="pop-row"><span>Speed</span><b>${orbitalSpeedKms(radiusKm).toFixed(2)} km/s</b></div>` +
    (sky
      ? `<div class="pop-row"><span>Elevation</span><b>${sky.el.toFixed(0)}°</b></div>` +
        `<div class="pop-row"><span>Azimuth</span><b>${sky.az.toFixed(0)}°</b></div>`
      : '') +
    `<div class="pop-row"><span>Signal</span><b>${signal}</b></div>` +
    `<div class="pop-row"><span>Observed</span><b>${sat.samples.length} · ${lastSeen}</b></div>`;
  popupEl.querySelector('.pop-close').addEventListener('click', hidePopup);
  popupEl.style.left = Math.min(window.innerWidth - 246, Math.max(8, sx + 14)) + 'px';
  popupEl.style.top = Math.min(window.innerHeight - 210, Math.max(8, sy + 14)) + 'px';
  popupEl.classList.remove('hidden');

  if (sat.orbit) {
    highlightGroup.add(orbitRing(sat.orbit, meta.color, 0.85));
  }
  if (sat.trail && sat.trail.length >= 2) {
    highlightGroup.add(arcLine(sat.trail.map((p) => new THREE.Vector3(p.x, p.y, p.z)), meta.color));
  }

  const halo = new THREE.Mesh(
    new THREE.SphereGeometry(620, 16, 12),
    new THREE.MeshBasicMaterial({ color: 0xffffff, wireframe: true, transparent: true, opacity: 0.55 }),
  );
  halo.position.copy(pos);
  highlightGroup.add(halo);
}

/** Hide the popup and clear the selection halo. */
function hidePopup() {
  popupEl.classList.add('hidden');
  clearGroup(highlightGroup);
}

/** Bind click-to-select (distinguished from a drag) and Escape-to-close. */
function bindPicking() {
  let downX = 0;
  let downY = 0;
  let downT = 0;
  renderer.domElement.addEventListener('pointerdown', (e) => {
    downX = e.clientX;
    downY = e.clientY;
    downT = performance.now();
  });
  renderer.domElement.addEventListener('pointerup', (e) => {
    if (Math.hypot(e.clientX - downX, e.clientY - downY) > 6 || performance.now() - downT > 500) {
      return; // a drag-rotate, not a click
    }
    const hit = pickAt(e.clientX, e.clientY);
    if (hit) showPopup(hit.sat, hit.pos, hit.sx, hit.sy);
    else hidePopup();
  });
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') hidePopup();
  });
}

/** Fetch the constellation for the current window (or demo data) and render. */
async function load() {
  setStatus('Loading constellation…');
  try {
    const data = DEMO ? demoData() : await fetchWindow(state.hours);
    if (!data.sats.length && !DEMO) {
      setStatus('No satellite observations in this window yet.');
      clearGroup(satGroup);
      clearGroup(markerGroup);
      return;
    }
    render(data);
  } catch (e) {
    setStatus(typeof e === 'string' ? e : 'Could not load constellation data.');
  }
}

/**
 * Fetch `/api/constellation` for a trailing window.
 *
 * @param {number} hours Trailing window length.
 * @returns {Promise<Object>} The constellation payload.
 */
async function fetchWindow(hours) {
  const end = new Date();
  const start = new Date(end.getTime() - hours * 3600 * 1000);
  const qs = `start=${start.toISOString()}&end=${end.toISOString()}`;
  const resp = await fetch(`/api/constellation?${qs}`);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw body.error || 'No GPS fix available to anchor the observer.';
  }
  return resp.json();
}

/**
 * Synthesise a constellation for offline development (`?demo`): a Colorado
 * observer plus satellites on inclined circular orbits, each sampled along a
 * visible arc.
 *
 * @returns {Object} A payload matching the `/api/constellation` shape.
 */
function demoData() {
  const R_EARTH = 6371;
  const obs = sphericalEcef(39.74, -104.99, R_EARTH);
  const defs = [[0, 26560, 8], [6, 25510, 6], [2, 29600, 6], [3, 27910, 5]];
  const sats = [];
  let svid = 1;
  for (const [gnssid, radius, n] of defs) {
    for (let i = 0; i < n; i++) {
      const inc = (50 + Math.random() * 12) * DEG;
      const raan = Math.random() * 2 * Math.PI;
      const phase0 = Math.random() * 2 * Math.PI;
      const samples = [];
      for (let k = 0; k < 24; k++) {
        const nu = phase0 + k * 0.04;
        const p = orbitPoint(radius, inc, raan, nu);
        samples.push({ t: null, x: p.x, y: p.y, z: p.z, snr: 18 + Math.random() * 28, used: Math.random() > 0.4 });
      }
      const nrm = orbitNormal(inc, raan);
      sats.push({ gnssid, svid: svid++, samples, orbit: { nx: nrm.x, ny: nrm.y, nz: nrm.z, radius_km: radius } });
    }
  }
  return {
    observer: { lat: 39.74, lon: -104.99, alt: 1600, x: obs.x, y: obs.y, z: obs.z, timestamp: new Date().toISOString() },
    earth_radius_km: R_EARTH,
    window: {},
    sats,
  };
}

/** Lat/lon on a sphere of radius r → ECEF vector (km). */
function sphericalEcef(latDeg, lonDeg, r) {
  const lat = latDeg * DEG;
  const lon = lonDeg * DEG;
  return new THREE.Vector3(
    r * Math.cos(lat) * Math.cos(lon),
    r * Math.cos(lat) * Math.sin(lon),
    r * Math.sin(lat),
  );
}

/** Orbit-plane normal for a demo orbit (matches orbitPoint's plane). */
function orbitNormal(inc, raan) {
  return new THREE.Vector3(
    Math.sin(raan) * Math.sin(inc),
    -Math.cos(raan) * Math.sin(inc),
    Math.cos(inc),
  );
}

/** A point on an inclined circular orbit (demo only). */
function orbitPoint(radius, inc, raan, nu) {
  const xo = radius * Math.cos(nu);
  const yo = radius * Math.sin(nu);
  const ci = Math.cos(inc);
  const si = Math.sin(inc);
  const cr = Math.cos(raan);
  const sr = Math.sin(raan);
  return new THREE.Vector3(
    xo * cr - yo * ci * sr,
    xo * sr + yo * ci * cr,
    yo * si,
  );
}

/** Wire the window-length, layer, and spin chips. */
function bindControls() {
  document.querySelectorAll('#windows .chip').forEach((b) => {
    b.addEventListener('click', () => {
      document.querySelectorAll('#windows .chip').forEach((c) => c.classList.remove('active'));
      b.classList.add('active');
      state.hours = Number(b.dataset.h);
      load();
    });
  });
  bindToggle('t-orbits', 'orbits', () => load());
  bindToggle('t-trails', 'trails', () => load());
  bindToggle('t-rays', 'rays', () => load());
  bindToggle('t-spin', 'spin', () => { controls.autoRotate = state.spin; });
  controls.autoRotate = state.spin;
}

/** Wire a toggle chip to a boolean state flag. */
function bindToggle(id, key, after) {
  const btn = document.getElementById(id);
  btn.classList.toggle('active', state[key]);
  btn.addEventListener('click', () => {
    state[key] = !state[key];
    btn.classList.toggle('active', state[key]);
    after();
  });
}

function onResize() {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

window.addEventListener('resize', onResize);
bindControls();
bindPicking();
buildEarth(6371);
load();
animate();
