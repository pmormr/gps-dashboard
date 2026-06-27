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
 * Per-metric display metadata, served by `/api/sensors` (`api/sensor_schema.py`
 * METRIC_META) so labels/units/scale live in one place server-side. Populated into
 * `META` each poll; unknown columns fall back to a generic rendering.
 */
let META = {};

/** Fallback meta for a column with no server entry (forward-compat). */
const FALLBACK_META = {
  label: '', unit: '', dec: 1, chart: true, color: '#94a3b8',
  convert: null, y_range: null, group: '',
};

/** Display metadata for a metric column, falling back for unknown columns. */
function metricMeta(key) {
  return META[key] || { ...FALLBACK_META, label: key };
}

/** Alt-unit conversions for the secondary readout (keyed by `meta.convert`). */
const CONVERTERS = {
  c_to_f: (v) => ({ value: v * 9 / 5 + 32, unit: '°F' }),
  kph_to_mph: (v) => ({ value: v * 0.621371, unit: 'mph' }),
  s_to_h: (v) => ({ value: v / 3600, unit: 'h' }),
};

/** Human labels for metric-group keys (current-values section headers). */
const GROUP_LABELS = {
  engine: 'Engine', temps: 'Temperatures', fuel: 'Fuel', electrical: 'Electrical',
  environment: 'Environment', battery: 'Battery', solar: 'Solar', dc: 'DC', ac: 'AC',
};

/** Display label for a group key, capitalized as a fallback. */
function groupLabel(group) {
  return GROUP_LABELS[group] || (group.charAt(0).toUpperCase() + group.slice(1));
}

/**
 * Ordered `[group, keys[]]` pairs for `keys` — first-seen group order and
 * within-group order preserved — so the current-values grid sections cleanly even
 * though storage order interleaves groups (e.g. OBD temps between engine metrics).
 */
function groupedKeys(keys) {
  const order = [];
  const byGroup = new Map();
  for (const key of keys) {
    const group = metricMeta(key).group || '';
    if (!byGroup.has(group)) { byGroup.set(group, []); order.push(group); }
    byGroup.get(group).push(key);
  }
  return order.map((group) => [group, byGroup.get(group)]);
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

/** Format one metric value with its unit (+ optional alt-unit), or an em dash. */
function formatValue(key, value) {
  if (value == null) return '<span class="metric-val">—</span>';
  const meta = metricMeta(key);
  const num = Number(value).toFixed(meta.dec);
  const unit = meta.unit ? `<span class="unit">${meta.unit}</span>` : '';
  const main = `<span class="metric-val">${num}${unit}</span>`;
  const convert = meta.convert && CONVERTERS[meta.convert];
  if (convert) {
    const alt = convert(Number(value));
    return `${main}<span class="metric-sub">${alt.value.toFixed(meta.dec)} ${alt.unit}</span>`;
  }
  return main;
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
    scales: { x: { time: true }, y: meta.y_range ? { range: meta.y_range } : {} },
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

  const pairs = groupedKeys(keys);
  const showHeads = pairs.length > 1;
  const grid = document.createElement('div');
  grid.className = 'metric-grid';
  const cells = {};
  for (const [group, groupKeys] of pairs) {
    if (showHeads && group) {
      const head = document.createElement('div');
      head.className = 'metric-group-head';
      head.textContent = groupLabel(group);
      grid.append(head);
    }
    for (const key of groupKeys) {
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
  }
  card.append(grid);

  const charts = document.createElement('div');
  charts.className = 'charts';
  const chartEls = {};
  for (const [, groupKeys] of pairs) {
    for (const key of groupKeys) {
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
  if (data.meta) META = data.meta;

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
