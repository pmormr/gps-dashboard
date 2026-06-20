/**
 * Live 3D satellite skyplot.
 *
 * Polls the read-only `/api/gpsd/sky` endpoint (gpsd's SKY message, no history
 * stored) and renders the visible constellation on a tilted wireframe
 * hemisphere in plain canvas — no 3D library, so it stays offline-clean.
 *
 * Each satellite is placed on a unit hemisphere by azimuth (compass angle) and
 * elevation (height), orthographically projected through a draggable
 * yaw/tilt camera. At tilt = 90° the projection collapses to the classic
 * flat top-down azimuth plot ("Top-down" button). Satellites are depth-sorted
 * so nearer ones occlude farther ones, with a vertical stem to the dome floor
 * as a height cue.
 */

'use strict';

const POLL_MS = 4000;
const DEMO = new URLSearchParams(location.search).has('demo');

/** Constellation → marker color. */
const GNSS = {
  GPS: '#22c55e',
  GLONASS: '#3b82f6',
  Galileo: '#f59e0b',
  BeiDou: '#ef4444',
  QZSS: '#a78bfa',
  SBAS: '#94a3b8',
  Other: '#64748b',
};

const DEG = Math.PI / 180;
const TILT_DEFAULT = 55 * DEG;
const TILT_MIN = 12 * DEG;
const TILT_MAX = 90 * DEG;

const canvas = document.getElementById('sky');
const ctx = canvas.getContext('2d');
const wrap = document.getElementById('sky-wrap');
const statusEl = document.getElementById('sky-status');

/** @type {{size:number, yaw:number, tilt:number, sats:Array, meta:Object|null, updatedAt:number|null}} */
const view = {
  size: 0,
  yaw: 0,
  tilt: TILT_DEFAULT,
  sats: [],
  meta: null,
  updatedAt: null,
};

/**
 * Rotate a world point (east, north, up) through the camera and project it to
 * screen space. At tilt = 90° the screen axes are (east, north) — top-down.
 *
 * @param {number} e East component of the unit vector.
 * @param {number} n North component.
 * @param {number} u Up component.
 * @returns {{x:number, y:number, depth:number}} Screen offset from center (in
 *   dome radii) and a depth where larger is nearer the camera.
 */
function camera(e, n, u) {
  const cy = Math.cos(view.yaw);
  const sy = Math.sin(view.yaw);
  const e1 = e * cy - n * sy;
  const n1 = e * sy + n * cy;
  const ct = Math.cos(view.tilt);
  const st = Math.sin(view.tilt);
  const vert = n1 * st + u * ct;
  const depth = -n1 * ct + u * st;
  return { x: e1, y: -vert, depth };
}

/**
 * Project an azimuth/elevation onto the screen via the hemisphere + camera.
 *
 * @param {number} az Azimuth in degrees, clockwise from North.
 * @param {number} el Elevation in degrees above the horizon.
 * @returns {{x:number, y:number, depth:number}} Camera-space screen point.
 */
function project(az, el) {
  const azr = az * DEG;
  const elr = el * DEG;
  const ce = Math.cos(elr);
  return camera(ce * Math.sin(azr), ce * Math.cos(azr), Math.sin(elr));
}

/** Resize the canvas to its container (square) at device pixel ratio. */
function resize() {
  const dpr = window.devicePixelRatio || 1;
  const size = Math.min(wrap.clientWidth, 480);
  view.size = size;
  canvas.style.height = size + 'px';
  canvas.width = Math.round(size * dpr);
  canvas.height = Math.round(size * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}

/**
 * Convert a camera-space point to absolute canvas pixels.
 *
 * @param {{x:number, y:number}} p Screen offset in dome radii.
 * @param {number} cx Canvas center x.
 * @param {number} cy Canvas center y.
 * @param {number} r Dome radius in pixels.
 * @returns {[number, number]} Pixel coordinates.
 */
function px(p, cx, cy, r) {
  return [cx + p.x * r, cy + p.y * r];
}

/** Draw the wireframe hemisphere: rings, meridians, zenith, compass labels. */
function drawDome(cx, cy, r) {
  ctx.lineWidth = 1;

  // Elevation rings (horizon + 30° + 60°).
  for (const { el, strong } of [{ el: 0, strong: true }, { el: 30 }, { el: 60 }]) {
    ctx.beginPath();
    for (let az = 0; az <= 360; az += 4) {
      const [x, y] = px(project(az, el), cx, cy, r);
      az === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.strokeStyle = strong ? 'rgba(148,163,184,0.55)' : 'rgba(148,163,184,0.22)';
    ctx.stroke();
  }

  // Azimuth meridians from horizon to zenith.
  ctx.strokeStyle = 'rgba(148,163,184,0.18)';
  for (let az = 0; az < 360; az += 45) {
    ctx.beginPath();
    for (let el = 0; el <= 90; el += 3) {
      const [x, y] = px(project(az, el), cx, cy, r);
      el === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  // Zenith marker.
  const [zx, zy] = px(project(0, 90), cx, cy, r);
  ctx.fillStyle = 'rgba(148,163,184,0.5)';
  ctx.beginPath();
  ctx.arc(zx, zy, 2, 0, 2 * Math.PI);
  ctx.fill();

  // Compass labels, nudged outward from center along the screen radial.
  ctx.fillStyle = 'rgba(226,232,240,0.85)';
  ctx.font = '600 13px -apple-system, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  for (const { az, label } of [
    { az: 0, label: 'N' }, { az: 90, label: 'E' },
    { az: 180, label: 'S' }, { az: 270, label: 'W' },
  ]) {
    const p = project(az, 0);
    const len = Math.hypot(p.x, p.y) || 1;
    const [x, y] = [cx + (p.x / len) * (Math.hypot(p.x, p.y) * r + 14),
                    cy + (p.y / len) * (Math.hypot(p.x, p.y) * r + 14)];
    ctx.fillText(label, x, y);
  }
}

/**
 * Draw one satellite: stem to the dome floor, then the marker.
 *
 * @param {Object} s Normalized satellite ({az, el, ss, used, gnss, prn}).
 * @param {number} cx Canvas center x.
 * @param {number} cy Canvas center y.
 * @param {number} r Dome radius in pixels.
 */
function drawSat(s, cx, cy, r) {
  const color = GNSS[s.gnss] || GNSS.Other;
  const top = project(s.az, s.el);
  const [tx, ty] = px(top, cx, cy, r);

  // Vertical stem from the satellite down to the dome floor (same az/el footprint).
  const azr = s.az * DEG;
  const ce = Math.cos(s.el * DEG);
  const foot = camera(ce * Math.sin(azr), ce * Math.cos(azr), 0);
  const [fx, fy] = px(foot, cx, cy, r);
  ctx.strokeStyle = 'rgba(148,163,184,0.28)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(fx, fy);
  ctx.lineTo(tx, ty);
  ctx.stroke();

  const ss = typeof s.ss === 'number' ? s.ss : 0;
  const radius = 4 + Math.max(0, Math.min(50, ss)) / 50 * 5;

  ctx.beginPath();
  ctx.arc(tx, ty, radius, 0, 2 * Math.PI);
  if (s.used) {
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = 'rgba(15,23,42,0.9)';
    ctx.stroke();
  } else {
    ctx.fillStyle = 'rgba(15,23,42,0.7)';
    ctx.fill();
    ctx.lineWidth = 1.75;
    ctx.strokeStyle = color;
    ctx.stroke();
  }

  if (s.prn != null) {
    ctx.fillStyle = 'rgba(226,232,240,0.7)';
    ctx.font = '9px -apple-system, sans-serif';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(String(s.prn), tx + radius + 2, ty);
  }
}

/** Repaint the whole plot from `view`. */
function draw() {
  const size = view.size;
  if (!size) return;
  ctx.clearRect(0, 0, size, size);
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 22;

  drawDome(cx, cy, r);

  // Painter's algorithm: farthest (smallest depth) first.
  const ordered = view.sats
    .map((s) => ({ s, depth: project(s.az, s.el).depth }))
    .sort((a, b) => a.depth - b.depth);
  for (const { s } of ordered) drawSat(s, cx, cy, r);
}

/** Push the live metadata into the stats strip. */
function renderStats() {
  const m = view.meta;
  const set = (id, val, cls) => {
    const el = document.getElementById(id);
    el.textContent = val;
    el.className = 'v' + (cls ? ' ' + cls : '');
  };
  if (!m) return;
  const usedCls = m.used >= 4 ? 'ok' : m.used > 0 ? 'warn' : 'err';
  set('st-sats', `${m.used} / ${m.seen}`, usedCls);
  set('st-fix', m.fix_label || '—', m.fix_mode >= 3 ? 'ok' : m.fix_mode >= 2 ? 'warn' : 'err');
  const dop = (v) => (typeof v === 'number' ? v.toFixed(1) : '—');
  set('st-hdop', dop(m.hdop));
  set('st-vdop', dop(m.vdop));
  set('st-pdop', dop(m.pdop));
  if (view.updatedAt) {
    const age = Math.round((Date.now() - view.updatedAt) / 1000);
    set('st-age', age <= 1 ? 'now' : `${age}s ago`);
  }
}

/** Build the constellation legend from the colors actually in use. */
function renderLegend() {
  const present = new Set(view.sats.map((s) => s.gnss));
  const order = ['GPS', 'GLONASS', 'Galileo', 'BeiDou', 'QZSS', 'SBAS', 'Other'];
  const items = order.filter((g) => present.has(g));
  const legend = document.getElementById('legend');
  legend.innerHTML =
    items.map((g) =>
      `<span class="leg"><span class="swatch" style="background:${GNSS[g]}"></span>${g}</span>`
    ).join('') +
    '<span class="leg-note">● filled = used in fix · ○ hollow = visible · size = signal strength</span>';
}

/** Show/hide the centered status overlay (connecting / no fix / offline). */
function setStatus(msg) {
  if (msg) {
    statusEl.textContent = msg;
    statusEl.classList.add('show');
  } else {
    statusEl.classList.remove('show');
  }
}

/** Synthetic sky for local rendering checks (`?demo`) — no gpsd in dev. */
let _demoSky = null;
function demoSky() {
  if (_demoSky) return _demoSky;
  const defs = [['GPS', 8], ['GLONASS', 6], ['Galileo', 6], ['BeiDou', 5], ['QZSS', 2], ['SBAS', 2]];
  const sats = [];
  let prn = 1;
  for (const [gnss, n] of defs) {
    for (let i = 0; i < n; i++) {
      sats.push({
        prn: prn++, gnss,
        az: Math.random() * 360,
        el: Math.random() * 84 + 4,
        ss: Math.random() * 38 + 12,
        used: Math.random() > 0.35,
      });
    }
  }
  _demoSky = {
    connected: true, fix_mode: 3, fix_label: '3D Fix',
    used: sats.filter((s) => s.used).length, seen: sats.length,
    hdop: 0.8, vdop: 1.2, pdop: 1.5, satellites: sats,
  };
  return _demoSky;
}

/** Fetch one sky snapshot, update state, and repaint. */
async function poll() {
  let data;
  try {
    data = DEMO ? demoSky() : await (await fetch('/api/gpsd/sky')).json();
  } catch (e) {
    setStatus('gpsd unreachable');
    return;
  }
  if (!data.connected) {
    setStatus('gpsd not connected');
    view.sats = [];
    draw();
    return;
  }
  view.meta = data;
  view.sats = data.satellites || [];
  view.updatedAt = Date.now();
  setStatus(view.sats.length ? '' : 'No satellites with a known position yet…');
  renderLegend();
  renderStats();
  draw();
}

/** Pointer drag → yaw (horizontal) + tilt (vertical), clamped. */
function bindDrag() {
  let dragging = false;
  let lastX = 0;
  let lastY = 0;
  canvas.addEventListener('pointerdown', (e) => {
    dragging = true;
    lastX = e.clientX;
    lastY = e.clientY;
    canvas.classList.add('dragging');
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    view.yaw += (e.clientX - lastX) * 0.01;
    view.tilt = Math.max(TILT_MIN, Math.min(TILT_MAX, view.tilt + (e.clientY - lastY) * 0.01));
    lastX = e.clientX;
    lastY = e.clientY;
    draw();
  });
  const end = () => { dragging = false; canvas.classList.remove('dragging'); };
  canvas.addEventListener('pointerup', end);
  canvas.addEventListener('pointercancel', end);
}

document.getElementById('btn-topdown').addEventListener('click', () => {
  view.tilt = TILT_MAX;
  view.yaw = 0;
  draw();
});
document.getElementById('btn-reset').addEventListener('click', () => {
  view.tilt = TILT_DEFAULT;
  view.yaw = 0;
  draw();
});

window.addEventListener('resize', resize);
bindDrag();
resize();
poll();
setInterval(poll, POLL_MS);
setInterval(renderStats, 1000);
