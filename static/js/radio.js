/**
 * Radio control head for the Icom ID-5100A (main band, over CI-V via rigctld).
 *
 * Polls `/api/radio/status` every 2s for the live readout (freq, mode, S-meter,
 * RX/TX flags) and reflects the rig's current tone/repeater state into the
 * controls. Writes go to `POST /api/radio/{freq,mode,tone,repeater}`. The radio
 * also changes from its own front panel, so the poll re-syncs control highlights —
 * but never clobbers an input the user is actively editing.
 */

'use strict';

const POLL_MS = 2000;

/** Standard CTCSS tones (Hz) the ID-5100 supports (from 1a dump_caps). */
const CTCSS_TONES = [
  67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0,
  103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2,
  151.4, 156.7, 159.8, 162.2, 165.5, 167.9, 171.3, 173.8, 177.3, 179.9, 183.5,
  186.2, 189.9, 192.8, 196.6, 199.5, 203.5, 206.5, 210.7, 218.1, 225.7, 229.1,
  233.6, 241.8, 250.3, 254.1,
];

const el = (id) => document.getElementById(id);

/** Whether an input element is the active, focused element. */
function isEditing(node) {
  return document.activeElement === node;
}

let toastTimer = null;
/** Flash a transient message at the bottom of the screen. */
function toast(message, isError = false) {
  const t = el('toast');
  t.textContent = message;
  t.className = `toast show${isError ? ' err' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = 'toast'), 2200);
}

/** POST JSON to a radio endpoint; toast the outcome. Returns true on success. */
async function post(path, body) {
  try {
    const resp = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      toast(data.error || `Failed (${resp.status})`, true);
      return false;
    }
    return true;
  } catch {
    toast('Could not reach the server', true);
    return false;
  }
}

/** Highlight the one button in a segmented group whose data-attr matches `value`. */
function setActive(groupId, attr, value) {
  for (const btn of el(groupId).querySelectorAll('button')) {
    btn.classList.toggle('active', btn.dataset[attr] === value);
  }
}

/** Mirror the current server state into the readout + control widgets. */
function render(s) {
  const banner = el('banner');
  if (!s.online) {
    banner.className = 'banner err';
    banner.textContent = `Radio offline — rigctld ${s.service || 'unreachable'}. ` +
      'Connect the CI-V cable and enable the radio-control service.';
    el('freq').textContent = '—';
    el('mode-badge').textContent = '—';
    el('smeter-fill').style.width = '0%';
    el('smeter-raw').textContent = '—';
    return;
  }
  banner.className = 'banner ok';
  banner.textContent = '● Connected';

  el('freq').textContent = (s.freq_hz / 1e6).toFixed(3);
  el('mode-badge').textContent = s.mode;
  el('freq-input').placeholder = `${(s.freq_hz / 1e6).toFixed(3)} MHz`;

  el('flag-rx').classList.toggle('on', !!s.dcd);
  el('flag-tx').classList.toggle('on', !!s.ptt);

  const raw = s.rawstr == null ? 0 : s.rawstr;
  el('smeter-fill').style.width = `${Math.min(100, (raw / 255) * 100)}%`;
  el('smeter-raw').textContent = s.rawstr == null ? '—' : `${Math.round(raw)} / 255`;

  setActive('mode-seg', 'mode', s.mode);
  setActive('tone-seg', 'tone', s.tone_mode);
  setActive('shift-seg', 'shift', s.rptr_shift);

  const sel = el('ctcss-select');
  if (s.ctcss_tone_hz != null && !isEditing(sel)) sel.value = String(s.ctcss_tone_hz);
  const offset = el('offset-input');
  if (s.rptr_offset_hz != null && !isEditing(offset)) offset.value = String(s.rptr_offset_hz / 1000);
}

/** One poll cycle. */
async function poll() {
  try {
    const resp = await fetch('/api/radio/status');
    render(await resp.json());
  } catch {
    el('banner').className = 'banner err';
    el('banner').textContent = 'Could not reach the server';
  }
}

/** The selected CTCSS frequency in Hz, as a number. */
function selectedTone() {
  return Number(el('ctcss-select').value);
}

/** Push the current tone mode + frequency to the rig. */
function applyTone(mode) {
  const body = mode === 'off' ? { mode } : { mode, hz: selectedTone() };
  return post('/api/radio/tone', body);
}

/** Push the current repeater shift + offset to the rig. */
function applyShift(shift) {
  const kHz = Number(el('offset-input').value);
  const body = shift === 'simplex' ? { shift } : { shift, offset_hz: Math.round(kHz * 1000) };
  return post('/api/radio/repeater', body);
}

function init() {
  // CTCSS dropdown.
  const sel = el('ctcss-select');
  for (const hz of CTCSS_TONES) {
    const opt = document.createElement('option');
    opt.value = String(hz);
    opt.textContent = `${hz.toFixed(1)} Hz`;
    sel.append(opt);
  }
  sel.value = '100';

  // Tune: frequency.
  const submitFreq = async () => {
    const mhz = Number(el('freq-input').value);
    if (!mhz || mhz <= 0) return;
    if (await post('/api/radio/freq', { hz: Math.round(mhz * 1e6) })) {
      el('freq-input').value = '';
      poll();
    }
  };
  el('freq-set').addEventListener('click', submitFreq);
  el('freq-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') submitFreq();
  });

  // Mode buttons.
  el('mode-seg').addEventListener('click', async (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    if (await post('/api/radio/mode', { mode: btn.dataset.mode })) poll();
  });

  // Tone mode + frequency.
  el('tone-seg').addEventListener('click', async (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    if (await applyTone(btn.dataset.tone)) poll();
  });
  sel.addEventListener('change', async () => {
    const active = el('tone-seg').querySelector('button.active');
    const mode = active ? active.dataset.tone : 'off';
    if (mode !== 'off') await applyTone(mode);
  });

  // Repeater shift + offset.
  el('shift-seg').addEventListener('click', async (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    if (await applyShift(btn.dataset.shift)) poll();
  });
  el('offset-set').addEventListener('click', async () => {
    const active = el('shift-seg').querySelector('button.active');
    const shift = active ? active.dataset.shift : 'plus';
    if (await applyShift(shift)) poll();
  });

  poll();
  setInterval(poll, POLL_MS);
}

init();
