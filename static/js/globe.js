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
 * Per satellite: a polyline arc through its samples (the observed track) plus a
 * dot at its latest position, coloured by constellation. A marker pins the van's
 * location, with optional sight-lines to the satellites currently in view. PC
 * browsers only (WebGL, mouse orbit/zoom). `?demo` synthesises a constellation
 * for offline development.
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

const container = document.getElementById('globe-container');
const statusEl = document.getElementById('status');
const infoEl = document.getElementById('info');
const legendEl = document.getElementById('legend');

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
scene.add(satGroup);
scene.add(markerGroup);

/** UI state: window hours, layer toggles, and per-constellation visibility. */
const state = {
  hours: 24,
  arcs: true,
  rays: true,
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
 * Rebuild the satellite arcs, dots, and sight-lines from a constellation payload.
 *
 * @param {Object} data `/api/constellation` response (observer + per-SV samples).
 */
function render(data) {
  buildEarthIfNeeded(data.earth_radius_km);
  clearGroup(satGroup);
  clearGroup(markerGroup);

  const obs = data.observer;
  const obsVec = new THREE.Vector3(obs.x, obs.y, obs.z);
  addMarker(obsVec);

  const seen = new Set();
  let sampleCount = 0;
  for (const sat of data.sats) {
    const meta = GNSS[sat.gnssid] || { name: 'Other', color: 0x64748b };
    seen.add(sat.gnssid);
    sampleCount += sat.samples.length;
    const pts = sat.samples.map((s) => new THREE.Vector3(s.x, s.y, s.z));
    const last = pts[pts.length - 1];

    const obj = new THREE.Group();
    obj.userData.gnssid = sat.gnssid;
    obj.visible = !state.hidden.has(sat.gnssid);

    if (state.arcs && pts.length >= 2) {
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      obj.add(new THREE.Line(geo, new THREE.LineBasicMaterial({
        color: meta.color, transparent: true, opacity: 0.55,
      })));
    }

    const snr = sat.samples[sat.samples.length - 1].snr || 0;
    const dot = new THREE.Mesh(
      new THREE.SphereGeometry(180 + Math.min(50, snr) * 9, 12, 10),
      new THREE.MeshBasicMaterial({ color: meta.color }),
    );
    dot.position.copy(last);
    obj.add(dot);

    if (state.rays) {
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
      sats.push({ gnssid, svid: svid++, samples });
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
  bindToggle('t-arcs', 'arcs', () => load());
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
buildEarth(6371);
load();
animate();
