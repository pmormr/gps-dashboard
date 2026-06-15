// Graylog-style time picker.
//
// State shape: { mode, anchor, window, from, to, live }
//   mode    'last' | 'around' | 'range'
//   anchor  Date — query datetime (irrelevant in 'range')
//   window  ms duration (irrelevant in 'range')
//   from    Date — start of explicit range
//   to      Date — end of explicit range
//   live    bool — when true, anchor follows now() and emits every 30s.
//                  Only meaningful in 'last' (forced false in other modes).
//
// (from, to) for the active state is derived deterministically via getRange().
// Subscribers register via onChange(fn); the picker also fires immediately on
// subscription so consumers get the initial range without an extra round.
const TimePicker = (() => {
  const PRESETS = [
    { label: '15m', ms: 15 * 60 * 1000 },
    { label: '1h',  ms: 60 * 60 * 1000 },
    { label: '6h',  ms: 6 * 60 * 60 * 1000 },
    { label: '24h', ms: 24 * 60 * 60 * 1000 },
    { label: '7d',  ms: 7 * 24 * 60 * 60 * 1000 },
    { label: '30d', ms: 30 * 24 * 60 * 60 * 1000 },
  ];

  const POLL_MS = 30 * 1000;

  let state = {
    mode: 'last',
    anchor: null,
    window: 24 * 60 * 60 * 1000,
    from: null,
    to: null,
    live: true,
  };

  let draft = null;       // popover-local edit buffer
  let pollTimer = null;
  const listeners = [];

  // ── Computed range ─────────────────────────────────────────────────────
  function getRange() {
    if (state.mode === 'range') {
      return {
        from: state.from,
        to: state.to,
        live: false,
        mode: state.mode,
        window: (state.to && state.from) ? (state.to - state.from) : state.window,
      };
    }
    const anchor = state.live || !state.anchor ? new Date() : state.anchor;
    if (state.mode === 'last') {
      return {
        from: new Date(+anchor - state.window),
        to: anchor,
        live: state.live,
        mode: state.mode,
        window: state.window,
      };
    }
    // around
    return {
      from: new Date(+anchor - state.window / 2),
      to: new Date(+anchor + state.window / 2),
      live: false,
      mode: state.mode,
      window: state.window,
    };
  }

  function emit() {
    const r = getRange();
    listeners.forEach(fn => fn(r));
  }

  function setState(partial, opts = {}) {
    state = { ...state, ...partial };
    if (state.mode !== 'last') state.live = false;
    updateTriggerLabel();
    updatePolling();
    if (!opts.silent) emit();
  }

  function onChange(fn) {
    listeners.push(fn);
    fn(getRange());
  }

  // ── Polling ────────────────────────────────────────────────────────────
  function updatePolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (state.live) pollTimer = setInterval(emit, POLL_MS);
  }

  // ── Trigger label ──────────────────────────────────────────────────────
  function fmtDuration(ms) {
    const preset = PRESETS.find(p => p.ms === ms);
    if (preset) return preset.label;
    if (ms % 86400000 === 0) return `${ms / 86400000}d`;
    if (ms % 3600000 === 0) return `${ms / 3600000}h`;
    return `${Math.round(ms / 60000)}m`;
  }

  function fmtAnchor(d) {
    return d.toLocaleString([], {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  }

  function triggerLabel() {
    if (state.mode === 'range') {
      if (!state.from || !state.to) return 'From → To';
      return `${fmtAnchor(state.from)} → ${fmtAnchor(state.to)}`;
    }
    const win = fmtDuration(state.window);
    if (state.mode === 'last') {
      return state.live
        ? `Live · Last ${win}`
        : `Last ${win} ending ${fmtAnchor(state.anchor || new Date())}`;
    }
    return `Around ${fmtAnchor(state.anchor || new Date())} ±${fmtDuration(state.window / 2)}`;
  }

  function updateTriggerLabel() {
    const el = document.getElementById('timepicker-trigger-label');
    if (el) el.textContent = triggerLabel();
  }

  // ── Popover ────────────────────────────────────────────────────────────
  function openPopover() {
    draft = { ...state };
    if (!draft.anchor) draft.anchor = new Date();
    if (draft.mode === 'range') {
      if (!draft.from) draft.from = new Date(Date.now() - draft.window);
      if (!draft.to) draft.to = new Date();
    }
    document.getElementById('timepicker-pop').classList.remove('hidden');
    renderDraft();
    positionPopover();
  }

  function closePopover() {
    document.getElementById('timepicker-pop').classList.add('hidden');
    draft = null;
  }

  function positionPopover() {
    if (window.innerWidth < 768) return; // mobile → CSS bottom sheet
    const pop = document.getElementById('timepicker-pop');
    const trigger = document.getElementById('timepicker-trigger');
    if (!pop || !trigger) return;
    const r = trigger.getBoundingClientRect();
    pop.style.top = (r.bottom + 6) + 'px';
    pop.style.left = r.left + 'px';
  }

  // <input type="datetime-local"> wants a YYYY-MM-DDTHH:MM string in the
  // user's *local* timezone (Date.toISOString() gives UTC and would be wrong).
  function dtLocal(d) {
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
         + `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function fromDtLocal(value) {
    return value ? new Date(value) : null;
  }

  function splitWindow(ms) {
    if (ms % 604800000 === 0) return [ms / 604800000, '604800000'];
    if (ms % 86400000 === 0)  return [ms / 86400000,  '86400000'];
    if (ms % 3600000 === 0)   return [ms / 3600000,   '3600000'];
    return [Math.max(1, Math.round(ms / 60000)), '60000'];
  }

  function renderDraft() {
    if (!draft) return;
    document.querySelectorAll('.timepicker-modes button').forEach(b => {
      b.classList.toggle('active', b.dataset.mode === draft.mode);
    });
    document.getElementById('timepicker-relative')
      .classList.toggle('hidden', draft.mode === 'range');
    document.getElementById('timepicker-absolute')
      .classList.toggle('hidden', draft.mode !== 'range');

    document.getElementById('timepicker-anchor').value = dtLocal(draft.anchor);
    const [num, unit] = splitWindow(draft.window);
    document.getElementById('timepicker-window-num').value = num;
    document.getElementById('timepicker-window-unit').value = unit;

    const liveEl = document.getElementById('timepicker-live');
    liveEl.checked = !!draft.live;
    liveEl.disabled = draft.mode !== 'last';
    document.querySelector('.timepicker-live-row')
      .classList.toggle('disabled', draft.mode !== 'last');

    if (draft.from) document.getElementById('timepicker-from').value = dtLocal(draft.from);
    if (draft.to)   document.getElementById('timepicker-to').value   = dtLocal(draft.to);
  }

  function readDraft() {
    const modeBtn = document.querySelector('.timepicker-modes button.active');
    const mode = modeBtn ? modeBtn.dataset.mode : draft.mode;
    const anchor = fromDtLocal(document.getElementById('timepicker-anchor').value);
    const num = parseInt(document.getElementById('timepicker-window-num').value, 10);
    const unit = parseInt(document.getElementById('timepicker-window-unit').value, 10);
    const windowMs = (num > 0 ? num : 1) * unit;
    const live = document.getElementById('timepicker-live').checked;
    const from = fromDtLocal(document.getElementById('timepicker-from').value);
    const to = fromDtLocal(document.getElementById('timepicker-to').value);
    draft = { mode, anchor, window: windowMs, live, from, to };
  }

  function applyDraft() {
    readDraft();
    if (draft.mode === 'range' && draft.from && draft.to && draft.from >= draft.to) {
      alert('"From" must be before "To".');
      return;
    }
    setState(draft);
    closePopover();
  }

  // ── Init ───────────────────────────────────────────────────────────────
  function init() {
    document.getElementById('timepicker-trigger')
      .addEventListener('click', e => {
        e.stopPropagation();
        const pop = document.getElementById('timepicker-pop');
        if (pop.classList.contains('hidden')) openPopover();
        else closePopover();
      });

    document.getElementById('timepicker-cancel')
      .addEventListener('click', closePopover);
    document.getElementById('timepicker-apply')
      .addEventListener('click', applyDraft);

    // Mobile backdrop click closes (the pop element fills the screen on mobile;
    // clicking outside the card lands on the pop itself, not on the card).
    document.getElementById('timepicker-pop').addEventListener('click', e => {
      if (e.target.id === 'timepicker-pop') closePopover();
    });

    document.querySelectorAll('.timepicker-modes button').forEach(btn => {
      btn.addEventListener('click', () => {
        draft.mode = btn.dataset.mode;
        if (draft.mode === 'range') {
          if (!draft.from) draft.from = new Date(Date.now() - draft.window);
          if (!draft.to)   draft.to   = new Date();
        }
        if (draft.mode !== 'last') draft.live = false;
        renderDraft();
      });
    });

    document.getElementById('timepicker-anchor-now').addEventListener('click', () => {
      document.getElementById('timepicker-anchor').value = dtLocal(new Date());
    });

    document.getElementById('timepicker-live').addEventListener('change', e => {
      if (draft) draft.live = e.target.checked;
    });

    // Preset chips: collapse to {mode:'last', window:preset, live:true} and
    // emit immediately. The popover closes; nothing else to confirm.
    document.querySelectorAll('.timepicker-presets button').forEach(btn => {
      btn.addEventListener('click', () => {
        const ms = parseInt(btn.dataset.preset, 10);
        setState({ mode: 'last', window: ms, live: true, anchor: null });
        closePopover();
      });
    });

    // Outside-click closes the popover. The trigger has its own handler
    // (with stopPropagation) so this never sees the open-click.
    document.addEventListener('click', e => {
      const pop = document.getElementById('timepicker-pop');
      const trigger = document.getElementById('timepicker-trigger');
      if (!pop || pop.classList.contains('hidden')) return;
      if (!pop.contains(e.target) && !trigger.contains(e.target)) closePopover();
    });

    window.addEventListener('resize', () => {
      const pop = document.getElementById('timepicker-pop');
      if (pop && !pop.classList.contains('hidden')) positionPopover();
    });

    updateTriggerLabel();
    updatePolling();
  }

  return { init, onChange, setState, getRange };
})();
