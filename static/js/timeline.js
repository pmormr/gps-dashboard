const Timeline = (() => {
  let allPoints = [];
  let slider = null;
  let pendingAnnotation = null;
  let currentMarks = {};
  let lastRange = null;

  function toTs(isoString) { return Math.floor(new Date(isoString).getTime() / 1000); }
  function fromTs(sec) { return new Date(sec * 1000).toISOString(); }

  function sliderLabel(sec) {
    return new Date(sec * 1000).toLocaleString([], {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  }

  // A moving vertex is an instant (match its timestamp); a stop spans its dwell
  // interval (match interval overlap), so brushing inside a long park selects
  // "parked here" even though the stop's own timestamp sits at the dwell start
  // and would otherwise fall outside a sub-window (mapview-redesign S2).
  function pointsInRange(lo, hi) {
    return allPoints.filter(p => {
      if (p.kind === 'stop' && p.dwell_start && p.dwell_end) {
        return toTs(p.dwell_end) >= lo && toTs(p.dwell_start) <= hi;
      }
      const t = toTs(p.timestamp);
      return t >= lo && t <= hi;
    });
  }

  function updateSliderLabels(lo, hi) {
    document.getElementById('tl-start-label').textContent = sliderLabel(lo);
    document.getElementById('tl-end-label').textContent = sliderLabel(hi);
  }

  function fmtDuration(mins) {
    if (mins < 60) return `${mins}m`;
    const h = Math.floor(mins / 60);
    if (h < 24) { const m = mins % 60; return m ? `${h}h ${m}m` : `${h}h`; }
    const d = Math.floor(h / 24);
    const rh = h % 24;
    return rh ? `${d}d ${rh}h` : `${d}d`;
  }

  // Draw each in-window stop as a block spanning its dwell interval on the slider
  // track, so a long park reads as a visible block instead of a dead zone where
  // dragging the handle does nothing (mapview-redesign S3). Window-based (shares
  // the slider's domain), so it changes only when the loaded window changes — not
  // on every brush. Sits in its own overlay div under the annotation bands.
  function renderStopOverlay() {
    const overlay = document.getElementById('tl-stop-overlay');
    if (!overlay) return;
    overlay.innerHTML = '';
    const win = getWindow();
    if (!win) return;
    const span = win.endMs - win.startMs;
    if (span <= 0) return;
    for (const p of allPoints) {
      if (p.kind !== 'stop' || !p.dwell_start || !p.dwell_end) continue;
      const sMs = new Date(p.dwell_start).getTime();
      const eMs = new Date(p.dwell_end).getTime();
      if (eMs < win.startMs || sMs > win.endMs) continue;
      const lo = Math.max(sMs, win.startMs);
      const hi = Math.min(eMs, win.endMs);
      const block = document.createElement('div');
      block.className = 'sl-stop-block';
      block.style.left = ((lo - win.startMs) / span) * 100 + '%';
      block.style.width = Math.max(0.4, ((hi - lo) / span) * 100) + '%';
      block.title = `Parked ${fmtDuration(Math.round((eMs - sMs) / 60000))}`;
      overlay.appendChild(block);
    }
  }

  function renderRange(followMap = false) {
    if (!slider) return;
    const [lo, hi] = slider.get().map(Number);
    updateSliderLabels(lo, hi);
    const pts = pointsInRange(lo, hi);
    MapView.showTrack(pts, { fitBounds: followMap, showEndpoints: pts.length > 1 });
    document.getElementById('tl-selection-count').textContent = `${pts.length} points selected`;

    pendingAnnotation = pts.length >= 2
      ? { start_time: fromTs(lo), end_time: fromTs(hi) }
      : null;
    document.getElementById('tl-create-btn').disabled = !pendingAnnotation;

    // "Zoom to Range" is meaningful only when the selection is narrower than the
    // loaded window — zooming to the full window would be a no-op refetch.
    const win = getWindow();
    const isSub = !!win && (lo > Math.floor(win.startMs / 1000) || hi < Math.floor(win.endMs / 1000));
    document.getElementById('tl-zoom-range-btn').disabled = !isSub;
  }

  async function loadRange(range) {
    const { from, to, live } = range;
    // "Bookmark Here" only makes sense against a live current position.
    document.getElementById('tl-bookmark-btn').classList.toggle('hidden', !live);
    if (!from || !to) return;

    // Treat live re-emits (anchor sliding forward) as continuations — don't
    // refit the map on every tick or the view jerks around.
    const isLiveTick = lastRange && lastRange.live && live &&
                       lastRange.mode === range.mode &&
                       lastRange.window === range.window;
    lastRange = range;

    document.getElementById('tl-status').textContent = 'Loading…';
    document.getElementById('tl-empty').classList.add('hidden');

    // /api/points reads the processed tier (track_points) and size-aware
    // decimates server-side (stops always kept, moving thinned by importance),
    // so the client just asks for the cap and renders whatever comes back.
    let data;
    try {
      data = await API.getPoints(from.toISOString(), to.toISOString(), 20000);
    } catch (e) {
      document.getElementById('tl-status').textContent = `Error: ${e.message}`;
      return;
    }

    allPoints = data.points;
    document.getElementById('tl-status').textContent =
      `${allPoints.length.toLocaleString()} pts${data.truncated ? ' (truncated)' : ''}`;

    if (!allPoints.length) {
      document.getElementById('tl-slider-wrap').classList.add('hidden');
      const emptyEl = document.getElementById('tl-empty');
      emptyEl.textContent = 'No GPS points for this range';
      emptyEl.classList.remove('hidden');
      MapView.clearTrack();
      if (slider) { slider.destroy(); slider = null; }
      renderStopOverlay();
      document.getElementById('tl-zoom-range-btn').disabled = true;
      return;
    }

    // The axis is the requested window, not the data extent: empty leading/
    // trailing time stays visible and selectable, and a lead-in dwell (whose
    // dwell_start predates the window) can't stretch it. Data renders onto this
    // axis; it never defines it (mapview-redesign S1).
    const lo = Math.floor(from.getTime() / 1000);
    const hi = Math.floor(to.getTime() / 1000);
    const step = Math.max(1, Math.floor((hi - lo) / 1000));

    if (slider) { slider.destroy(); slider = null; }
    const el = document.getElementById('tl-slider');
    slider = noUiSlider.create(el, {
      start: [lo, hi],
      connect: true,
      range: { min: lo, max: hi <= lo ? lo + 1 : hi },
      step,
    });
    slider.on('update', () => renderRange(false));
    document.getElementById('tl-slider-wrap').classList.remove('hidden');
    renderStopOverlay();

    MapView.showTrack(allPoints, { fitBounds: !isLiveTick, showEndpoints: false });
    Annotations.renderOverlays(allPoints);
  }

  function getPoints() {
    return allPoints;
  }

  // The currently loaded time window in ms (the requested [from, to], i.e. the
  // slider's domain), independent of where data actually exists. Overlays drawn
  // on the slider track must share this basis or they drift off the handle.
  function getWindow() {
    if (!lastRange || !lastRange.from || !lastRange.to) return null;
    return { startMs: lastRange.from.getTime(), endMs: lastRange.to.getTime() };
  }

  async function zoomToCurrentLocation() {
    try {
      const pt = await API.getPointsLatest();
      if (pt && pt.lat != null && pt.lon != null) {
        MapView.zoomTo(pt.lat, pt.lon, 17);
      }
    } catch (_) {}
  }

  function openAnnotationForm() {
    if (!pendingAnnotation) return;
    const isPoint = !pendingAnnotation.end_time;
    document.getElementById('annotation-form-title').textContent =
      isPoint ? 'Bookmark' : 'Create Range';
    document.getElementById('annotation-name-input').value = '';
    document.getElementById('annotation-notes-input').value = '';
    const whenEl = document.getElementById('annotation-form-when');
    if (whenEl) {
      const start = new Date(pendingAnnotation.start_time);
      whenEl.textContent = isPoint
        ? `At ${start.toLocaleString()}`
        : `${start.toLocaleString()} → ${new Date(pendingAnnotation.end_time).toLocaleString()}`;
    }
    document.getElementById('annotation-form-overlay').classList.remove('hidden');
    document.getElementById('annotation-name-input').focus();
  }

  function closeAnnotationForm() {
    document.getElementById('annotation-form-overlay').classList.add('hidden');
  }

  async function saveAnnotation() {
    const name = document.getElementById('annotation-name-input').value.trim();
    if (!name) { document.getElementById('annotation-name-input').focus(); return; }
    if (!pendingAnnotation) return;

    const notes = document.getElementById('annotation-notes-input').value.trim();
    const body = { ...pendingAnnotation, name, notes };
    // Range form passes end_time; point form does not. Backend treats a
    // missing or null end_time as a point bookmark.
    if (!body.end_time) delete body.end_time;
    try {
      await API.createAnnotation(body);
      closeAnnotationForm();
      Annotations.reload();
    } catch (e) {
      alert(`Failed to save annotation: ${e.message}`);
    }
  }

  // Bookmark the current spot: a one-tap point bookmark at the latest GPS fix's
  // time, so it lands on the real current position. Live-only (loadRange shows
  // the button only when the window is live), auto-named and form-less so it's
  // usable while driving — rename later from the annotations drawer.
  async function bookmarkCurrent() {
    const btn = document.getElementById('tl-bookmark-btn');
    let ts;
    try {
      const pt = await API.getPointsLatest();
      ts = pt && pt.timestamp ? pt.timestamp : new Date().toISOString();
    } catch (_) {
      ts = new Date().toISOString();
    }
    const name = 'Bookmark · ' + new Date(ts).toLocaleString([], {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
    try {
      await API.createAnnotation({ name, start_time: ts });
      const orig = btn.textContent;
      btn.textContent = '✓ Bookmarked';
      btn.disabled = true;
      setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 1200);
      Annotations.reload();
    } catch (e) {
      alert(`Bookmark failed: ${e.message}`);
    }
  }

  function fmtMarkTime(isoStr) {
    if (!isoStr) return '—';
    const d = new Date(isoStr);
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' +
           d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function updateMarkUI() {
    const s = currentMarks.start;
    const e = currentMarks.end;
    const hasBoth = s && e;
    const statusEl = document.getElementById('tl-mark-status');
    statusEl.textContent = (s || e) ? `S: ${fmtMarkTime(s)}  E: ${fmtMarkTime(e)}` : '';
    document.getElementById('tl-use-marks-btn').classList.toggle('hidden', !hasBoth);
  }

  async function loadMarks() {
    try {
      currentMarks = await API.getMarks();
    } catch (_) {
      currentMarks = {};
    }
    updateMarkUI();
  }

  async function handleMark(marker) {
    try {
      currentMarks = await API.markTimestamp(marker);
      updateMarkUI();
    } catch (e) {
      alert(`Mark failed: ${e.message}`);
    }
  }

  function useMarks() {
    const s = currentMarks.start;
    const e = currentMarks.end;
    if (!s || !e) return;
    // Marks are explicit timestamps the user set — jump the picker straight
    // to a `range` mode framing them.
    TimePicker.setState({
      mode: 'range',
      from: new Date(s),
      to: new Date(e),
      live: false,
    });
  }

  // Narrow the loaded window to the slider's current selection and re-fetch.
  // /api/points is size-aware decimated, so the same vertex budget over a
  // smaller window yields more detail — a quick "zoom in for granularity".
  // Reads the live slider (cf. useMarks, which reads the persisted marks).
  function zoomToRange() {
    if (!slider) return;
    const [lo, hi] = slider.get().map(Number);
    if (hi <= lo) return;
    TimePicker.setState({
      mode: 'range',
      from: new Date(lo * 1000),
      to: new Date(hi * 1000),
      live: false,
    });
  }

  function init() {
    document.getElementById('tl-create-btn').addEventListener('click', openAnnotationForm);
    document.getElementById('tl-bookmark-btn').addEventListener('click', bookmarkCurrent);
    document.getElementById('tl-zoom-range-btn').addEventListener('click', zoomToRange);
    document.getElementById('annotation-form-cancel').addEventListener('click', closeAnnotationForm);
    document.getElementById('annotation-form-save').addEventListener('click', saveAnnotation);
    document.getElementById('annotation-form-overlay').addEventListener('click', e => {
      if (e.target === e.currentTarget) closeAnnotationForm();
    });
    document.getElementById('annotation-name-input').addEventListener('keydown', e => {
      if (e.key === 'Enter') saveAnnotation();
      if (e.key === 'Escape') closeAnnotationForm();
    });

    document.getElementById('tl-mark-start-btn').addEventListener('click', () => handleMark('start'));
    document.getElementById('tl-mark-end-btn').addEventListener('click', () => handleMark('end'));
    document.getElementById('tl-use-marks-btn').addEventListener('click', useMarks);

    // Marks panel toggle (mirrors the Labels panel pattern).
    const marksPanel = document.getElementById('marks-panel');
    document.getElementById('marks-panel-toggle').addEventListener('click', () => {
      marksPanel.classList.toggle('collapsed');
    });
    document.getElementById('tl-zoom-here-btn').addEventListener('click', zoomToCurrentLocation);

    loadMarks();
    TimePicker.onChange(loadRange);
  }

  return { init, getPoints, getWindow };
})();
