/**
 * Sensor viewer: current values + trend charts.
 *
 * Polls the read-only JSON API (`/api/sensors`, `/api/sensors/<id>/readings`)
 * every 30s and renders, per sensor, a current-values grid plus a stack of
 * small uPlot trend charts. Reads from the logged DB — no MQTT/websockets — so
 * it works regardless of the broker's WS support. Charts are built once per
 * sensor and updated in place so range/zoom state survives a refresh.
 */

'use strict';

const POLL_MS = 30000;

/**
 * Display metadata per metric column. Unknown columns fall back to a generic
 * rendering (raw key as label, one decimal, charted).
 */
const METRICS = {
  temp_c: { label: 'Temp', unit: '°C', dec: 1, chart: true, color: '#f87171' },
  humidity_pct: { label: 'Humidity', unit: '%', dec: 1, chart: true, color: '#38bdf8' },
  pressure_hpa: { label: 'Pressure', unit: 'hPa', dec: 1, chart: true, color: '#a78bfa' },
  iaq: { label: 'IAQ', unit: '', dec: 0, chart: true, color: '#34d399' },
  iaq_accuracy: { label: 'IAQ acc', unit: '/3', dec: 0, chart: false, color: '#94a3b8' },
  co2_equivalent: { label: 'CO₂-eq', unit: 'ppm', dec: 0, chart: true, color: '#fbbf24' },
  breath_voc_equivalent: { label: 'Breath VOC', unit: 'ppm', dec: 2, chart: true, color: '#fb923c' },
  gas_ohms: { label: 'Gas', unit: 'Ω', dec: 0, chart: true, color: '#22d3ee' },
};

/** Per-metric display metadata, with a fallback for unknown columns. */
function metricMeta(key) {
  return METRICS[key] || { label: key, unit: '', dec: 1, chart: true, color: '#94a3b8' };
}

const state = {
  rangeHours: 24,
  /** sensorId -> { card, dot, name, age, cells:{metric:el}, chartEls:{metric:el}, charts:{metric:uPlot}, type } */
  models: new Map(),
};

/** Format an age in seconds as a compact human string. */
function formatAge(seconds) {
  if (seconds == null) return '—';
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

/** Parse a canonical UTC timestamp ("…Z") to epoch seconds. */
function tsToEpoch(ts) {
  return Date.parse(ts) / 1000;
}

/** Seconds between an ISO timestamp and now, or null if absent/unparseable. */
function ageSeconds(ts) {
  if (!ts) return null;
  const ms = Date.parse(ts);
  return Number.isNaN(ms) ? null : (Date.now() - ms) / 1000;
}

/**
 * Liveness dot class for a sensor: the broker's last-known status, downgraded
 * to "warn" when readings have gone stale even though status still says online.
 */
function dotClass(sensor) {
  const age = ageSeconds(sensor.last_seen);
  if (sensor.status === 'offline') return 'err';
  if (sensor.status === 'online') return age != null && age > 90 ? 'warn' : 'ok';
  return 'unknown';
}

/** Format one metric value with its unit, or an em dash when null. */
function formatValue(key, value) {
  if (value == null) return '<span class="metric-val">—</span>';
  const meta = metricMeta(key);
  const num = Number(value).toFixed(meta.dec);
  const unit = meta.unit ? `<span class="unit">${meta.unit}</span>` : '';
  return `<span class="metric-val">${num}${unit}</span>`;
}

/** Ordered metric keys to display for a sensor, from the server's type map. */
function metricKeys(typeMetrics, type, latest) {
  if (typeMetrics && typeMetrics[type]) return typeMetrics[type].metrics;
  return latest ? Object.keys(latest).filter((k) => k !== 'timestamp') : [];
}

/** Build a small dark-themed uPlot in `el` for a single charted metric. */
function makeChart(el, meta) {
  const opts = {
    width: el.clientWidth || 320,
    height: 110,
    cursor: { y: false },
    legend: { show: false },
    scales: { x: { time: true } },
    axes: [
      { stroke: '#94a3b8', grid: { stroke: '#1e293b' }, ticks: { stroke: '#334155' },
        font: '11px sans-serif' },
      { stroke: '#94a3b8', grid: { stroke: '#1e293b' }, ticks: { stroke: '#334155' },
        font: '11px sans-serif', size: 50 },
    ],
    series: [
      {},
      { stroke: meta.color, width: 1.5, points: { show: false },
        value: (_, v) => (v == null ? '—' : v.toFixed(meta.dec)) },
    ],
  };
  return new uPlot(opts, [[], []], el);
}

/** Create the DOM skeleton + charts for one sensor, returning its model. */
function buildSensorModel(sensor, keys) {
  const card = document.createElement('div');
  card.className = 'card';

  const head = document.createElement('div');
  head.className = 'sensor-head';
  const dot = document.createElement('div');
  dot.className = 'dot';
  const name = document.createElement('div');
  name.className = 'sensor-name';
  const age = document.createElement('div');
  age.className = 'sensor-age';
  head.append(dot, name, age);
  card.append(head);

  const grid = document.createElement('div');
  grid.className = 'metric-grid';
  const cells = {};
  for (const key of keys) {
    const cell = document.createElement('div');
    cell.className = 'metric-cell';
    const label = document.createElement('div');
    label.className = 'metric-label';
    label.textContent = metricMeta(key).label;
    const val = document.createElement('div');
    cell.append(label, val);
    grid.append(cell);
    cells[key] = val;
  }
  card.append(grid);

  const charts = document.createElement('div');
  charts.className = 'charts';
  const chartEls = {};
  for (const key of keys) {
    if (!metricMeta(key).chart) continue;
    const block = document.createElement('div');
    block.className = 'chart-block';
    const label = document.createElement('div');
    label.className = 'chart-label';
    const meta = metricMeta(key);
    label.textContent = meta.unit ? `${meta.label} (${meta.unit})` : meta.label;
    const plot = document.createElement('div');
    block.append(label, plot);
    charts.append(block);
    chartEls[key] = plot;
  }
  card.append(charts);

  document.getElementById('sensors').append(card);

  const chartInstances = {};
  for (const key of Object.keys(chartEls)) {
    chartInstances[key] = makeChart(chartEls[key], metricMeta(key));
  }

  return { card, dot, name, age, cells, chartEls, charts: chartInstances, type: sensor.type };
}

/** Update a sensor's head + current-value cells from a registry entry. */
function updateCurrentValues(model, sensor) {
  model.dot.className = `dot ${dotClass(sensor)}`;
  const sub = sensor.location ? ` · ${sensor.location}` : '';
  model.name.innerHTML =
    `${sensor.node} <span class="sub">${sensor.type}${sub}</span>`;
  model.age.textContent = `${sensor.status} · ${formatAge(ageSeconds(sensor.last_seen))}`;

  const latest = sensor.latest || {};
  for (const [key, el] of Object.entries(model.cells)) {
    el.innerHTML = formatValue(key, latest[key]);
  }
}

/** Fetch history for one sensor over the active range and update its charts. */
async function loadHistory(sensorId, model) {
  const end = new Date().toISOString();
  const start = new Date(Date.now() - state.rangeHours * 3600 * 1000).toISOString();
  const url = `/api/sensors/${sensorId}/readings?start=${encodeURIComponent(start)}` +
    `&end=${encodeURIComponent(end)}`;
  let data;
  try {
    const resp = await fetch(url);
    if (!resp.ok) return;
    data = await resp.json();
  } catch {
    return;
  }
  const xs = data.readings.map((r) => tsToEpoch(r.timestamp));
  for (const [key, chart] of Object.entries(model.charts)) {
    const ys = data.readings.map((r) => (r[key] == null ? null : Number(r[key])));
    chart.setData([xs, ys]);
  }
}

/** One poll cycle: refresh the registry, then each sensor's history. */
async function poll() {
  const note = document.getElementById('status-note');
  let data;
  try {
    const resp = await fetch('/api/sensors');
    data = await resp.json();
  } catch {
    note.textContent = 'Failed to reach the server';
    return;
  }

  const sensors = data.sensors || [];
  if (sensors.length === 0) {
    document.getElementById('sensors').innerHTML =
      '<div class="card"><div class="empty">No sensors registered yet. ' +
      'Once a node publishes to the MQTT bus and ingest records it, it shows up here.</div></div>';
    note.textContent = `Checked ${new Date().toLocaleTimeString()}`;
    return;
  }

  const historyLoads = [];
  for (const sensor of sensors) {
    const keys = metricKeys(data.metrics, sensor.type, sensor.latest);
    let model = state.models.get(sensor.id);
    if (!model) {
      model = buildSensorModel(sensor, keys);
      state.models.set(sensor.id, model);
    }
    updateCurrentValues(model, sensor);
    historyLoads.push(loadHistory(sensor.id, model));
  }
  await Promise.all(historyLoads);
  note.textContent = `Updated ${new Date().toLocaleTimeString()} · last ${state.rangeHours}h`;
}

/** Reload every sensor's chart history for the current range. */
function reloadAllHistory() {
  const loads = [];
  for (const [id, model] of state.models) loads.push(loadHistory(id, model));
  return Promise.all(loads);
}

function initRangeBar() {
  document.getElementById('range-bar').addEventListener('click', (e) => {
    const btn = e.target.closest('.range-btn');
    if (!btn) return;
    state.rangeHours = Number(btn.dataset.hours);
    for (const b of document.querySelectorAll('.range-btn')) {
      b.classList.toggle('active', b === btn);
    }
    reloadAllHistory();
  });
}

let resizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    for (const model of state.models.values()) {
      for (const [key, chart] of Object.entries(model.charts)) {
        chart.setSize({ width: model.chartEls[key].clientWidth, height: 110 });
      }
    }
  }, 150);
});

initRangeBar();
poll();
setInterval(poll, POLL_MS);
