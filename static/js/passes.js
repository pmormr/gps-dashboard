/**
 * Upcoming satellite passes schedule (/passes).
 *
 * Fetches `/api/passes` for the chosen horizon and mask elevation and renders
 * one card per predicted pass — rise, peak, and set times with compass azimuths
 * and a live countdown. The predictions come from orbits fit to the logged az/el
 * (see common/orbits.py); this page is just the readout. Re-fetches on a timer
 * and whenever a control changes.
 */

/** gpsd gnssid → display colour (matches the globe/skyplot palette). */
const GNSS_COLOR = {
  0: '#22c55e', // GPS
  1: '#94a3b8', // SBAS
  2: '#f59e0b', // Galileo
  3: '#ef4444', // BeiDou
  5: '#a78bfa', // QZSS
  6: '#3b82f6', // GLONASS
};

const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];

const state = { hours: 12, mask: 5 };
let timer = null;

const summaryEl = document.getElementById('summary');
const scheduleEl = document.getElementById('schedule');

/** Azimuth in degrees → 16-point compass abbreviation. */
function compass(az) {
  return COMPASS[Math.round(az / 22.5) % 16];
}

/** Local clock HH:MM for a Unix-seconds instant. */
function clock(unix) {
  return new Date(unix * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** A signed minute/hour gap like "12m", "2h 05m" for a duration in seconds. */
function humanGap(seconds) {
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

/** The "when" label + class for a pass relative to now. */
function whenLabel(p, nowUnix) {
  if (p.in_progress) return { text: `up now · sets in ${humanGap(p.set_unix - nowUnix)}`, cls: 'up' };
  const until = p.rise_unix - nowUnix;
  return { text: `rises in ${humanGap(until)}`, cls: until < 1800 ? 'soon' : '' };
}

/** Render one pass card. */
function passCard(p, nowUnix) {
  const color = GNSS_COLOR[p.gnssid] || '#64748b';
  const when = whenLabel(p, nowUnix);
  const snr = p.max_snr != null ? `${p.max_snr.toFixed(0)} dB·Hz` : '—';
  return `
    <div class="pass">
      <div class="pass-head">
        <span class="sat"><span class="sw" style="background:${color}"></span>${p.name}</span>
        <span class="sys">${p.system}</span>
        <span class="when ${when.cls}">${when.text}</span>
      </div>
      <div class="legs">
        <div class="leg">
          <div class="leg-name">Rise</div>
          <div class="leg-time">${clock(p.rise_unix)}</div>
          <div class="leg-sub">${compass(p.rise_az)} · ${p.rise_az.toFixed(0)}°</div>
        </div>
        <div class="leg peak">
          <div class="leg-name">Peak ${p.peak_el.toFixed(0)}°</div>
          <div class="leg-time">${clock(p.peak_unix)}</div>
          <div class="leg-sub">${compass(p.peak_az)} · ${p.peak_az.toFixed(0)}°</div>
        </div>
        <div class="leg">
          <div class="leg-name">Set</div>
          <div class="leg-time">${clock(p.set_unix)}</div>
          <div class="leg-sub">${compass(p.set_az)} · ${p.set_az.toFixed(0)}°</div>
        </div>
      </div>
      <div class="pass-foot">
        <span>Duration <b>${humanGap(p.duration_s)}</b></span>
        <span>Signal <b>${snr}</b>${p.used ? ' · used' : ''}</span>
      </div>
    </div>`;
}

/** Render the whole response. */
function render(data) {
  const nowUnix = Date.now() / 1000;
  const o = data.observer;
  const updated = new Date().toLocaleTimeString();
  summaryEl.innerHTML =
    `<b>${data.passes.length}</b> passes in next <b>${data.horizon_hours}h</b> · ` +
    `<b>${data.counts.fit}</b>/<b>${data.counts.observed}</b> satellites fit · ` +
    `mask <b>${data.mask_deg}°</b><br>` +
    `Observer <b>${o.lat.toFixed(3)}°, ${o.lon.toFixed(3)}°</b> · updated <b>${updated}</b>`;

  if (!data.passes.length) {
    scheduleEl.innerHTML = data.counts.fit
      ? '<div class="empty">No passes above the mask in this window. Widen the horizon or lower the mask.</div>'
      : '<div class="empty">No orbits fit yet — a few hours of logged satellite observations are needed before passes can be predicted.</div>';
    return;
  }
  scheduleEl.innerHTML = data.passes.map((p) => passCard(p, nowUnix)).join('');
}

/** Fetch and render the schedule for the current controls. */
async function load() {
  try {
    const resp = await fetch(`/api/passes?hours=${state.hours}&mask=${state.mask}`);
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      summaryEl.textContent = body.error || 'Could not load passes.';
      scheduleEl.innerHTML = '';
      return;
    }
    render(await resp.json());
  } catch {
    summaryEl.textContent = 'Could not load passes.';
  }
}

/** Schedule the next auto-refresh, replacing any pending one. */
function scheduleRefresh() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(async () => {
    await load();
    scheduleRefresh();
  }, 60000);
}

/** Wire a chip group to a state key, reloading on change. */
function bindChips(groupId, key, parse) {
  document.getElementById(groupId).querySelectorAll('.chip').forEach((b) => {
    b.addEventListener('click', () => {
      document.querySelectorAll(`#${groupId} .chip`).forEach((c) => c.classList.remove('active'));
      b.classList.add('active');
      state[key] = parse(b);
      load();
      scheduleRefresh();
    });
  });
}

bindChips('horizon', 'hours', (b) => Number(b.dataset.h));
bindChips('mask', 'mask', (b) => Number(b.dataset.m));
document.getElementById('refresh-now').addEventListener('click', (e) => {
  e.preventDefault();
  load();
  scheduleRefresh();
});

load();
scheduleRefresh();
