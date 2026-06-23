const Timeline = (() => {
  let allPoints = [];
  let pendingAnnotation = null;
  let editId = null;          // annotation id being edited (null = create mode)
  let currentMarks = {};
  let lastRange = null;

  function windowLabel(ms) {
    return new Date(ms).toLocaleString([], {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  }

  function updateSliderLabels(loMs, hiMs) {
    document.getElementById('tl-start-label').textContent = windowLabel(loMs);
    document.getElementById('tl-end-label').textContent = windowLabel(hiMs);
  }

  // A moving vertex is an instant (match its timestamp); a stop spans its dwell
  // interval (match interval overlap), so brushing inside a long park selects
  // "parked here" even though the stop's own timestamp sits at the dwell start
  // and would otherwise fall outside a sub-window (mapview-redesign S2). All ms.
  function pointsInRange(loMs, hiMs) {
    return allPoints.filter(p => {
      if (p.kind === 'stop' && p.dwell_start && p.dwell_end) {
        return new Date(p.dwell_end).getTime() >= loMs &&
               new Date(p.dwell_start).getTime() <= hiMs;
      }
      const t = new Date(p.timestamp).getTime();
      return t >= loMs && t <= hiMs;
    });
  }

  // Render the current brush selection: trail/map, the points-selected count, and
  // the Create Range / Zoom to Range enable-gating. Runs on the initial window
  // load (followMap = fit, unless a live tick) and on every TimeStrip brush
  // (followMap = false). Endpoint markers and Zoom to Range appear only when the
  // selection is a strict sub-range of the loaded window.
  function renderRange(followMap = false) {
    const sel = TimeStrip.getSelection();
    if (!sel) return;
    const { loMs, hiMs } = sel;
    updateSliderLabels(loMs, hiMs);
    const pts = pointsInRange(loMs, hiMs);
    const isSub = !!lastRange && (loMs > lastRange.from.getTime() || hiMs < lastRange.to.getTime());
    MapView.showTrack(pts, { fitBounds: followMap, showEndpoints: isSub && pts.length > 1 });
    document.getElementById('tl-selection-count').textContent = `${pts.length} points selected`;

    pendingAnnotation = pts.length >= 2
      ? { start_time: new Date(loMs).toISOString(), end_time: new Date(hiMs).toISOString() }
      : null;
    document.getElementById('tl-create-btn').disabled = !pendingAnnotation;
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
      document.getElementById('tl-strip-wrap').classList.add('hidden');
      const emptyEl = document.getElementById('tl-empty');
      emptyEl.textContent = 'No GPS points for this range';
      emptyEl.classList.remove('hidden');
      MapView.clearTrack();
      document.getElementById('tl-zoom-range-btn').disabled = true;
      return;
    }

    // The axis is the requested window, not the data extent: empty leading/
    // trailing time stays visible and selectable, and a lead-in dwell (whose
    // dwell_start predates the window) can't stretch it. Data renders onto this
    // axis via the TimeStrip canvas; it never defines it (mapview-redesign S1).
    document.getElementById('tl-strip-wrap').classList.remove('hidden');
    TimeStrip.setData({ startMs: from.getTime(), endMs: to.getTime(), points: allPoints });

    // Initial render of the full-window selection (fit the map unless this is a
    // live tick). Subsequent brushes re-enter via TimeStrip.onBrush below.
    renderRange(!isLiveTick);
    Annotations.renderOverlays(allPoints);
  }

  function getPoints() {
    return allPoints;
  }

  async function zoomToCurrentLocation() {
    try {
      const pt = await API.getPointsLatest();
      if (pt && pt.lat != null && pt.lon != null) {
        MapView.zoomTo(pt.lat, pt.lon, 17);
      }
    } catch (_) {}
  }

  // Shared annotation modal: shows a "when" line (read-only) + name/notes. Used
  // for both create (from the slider selection) and edit (from the drawer); the
  // `editId` state routes saveAnnotation between POST and PATCH.
  function fmtAnnotationWhen(startTime, endTime) {
    const start = new Date(startTime);
    return endTime
      ? `${start.toLocaleString()} → ${new Date(endTime).toLocaleString()}`
      : `At ${start.toLocaleString()}`;
  }

  // Only reached for ranges now (the strip's Create Range); point bookmarks are
  // created form-lessly by bookmarkCurrent, so there's no point branch here.
  function openAnnotationForm() {
    if (!pendingAnnotation) return;
    editId = null;
    document.getElementById('annotation-form-title').textContent = 'Create Range';
    document.getElementById('annotation-name-input').value = '';
    document.getElementById('annotation-notes-input').value = '';
    const whenEl = document.getElementById('annotation-form-when');
    if (whenEl) whenEl.textContent = fmtAnnotationWhen(pendingAnnotation.start_time, pendingAnnotation.end_time);
    document.getElementById('annotation-form-overlay').classList.remove('hidden');
    document.getElementById('annotation-name-input').focus();
  }

  // Edit an existing annotation's name + notes (its bounds stay put). Called from
  // the annotations drawer's ✎ button.
  function openEditAnnotation(ann) {
    pendingAnnotation = null;
    editId = ann.id;
    document.getElementById('annotation-form-title').textContent = 'Edit Annotation';
    document.getElementById('annotation-name-input').value = ann.name || '';
    document.getElementById('annotation-notes-input').value = ann.notes || '';
    const whenEl = document.getElementById('annotation-form-when');
    if (whenEl) whenEl.textContent = fmtAnnotationWhen(ann.start_time, ann.end_time);
    document.getElementById('annotation-form-overlay').classList.remove('hidden');
    document.getElementById('annotation-name-input').focus();
  }

  function closeAnnotationForm() {
    document.getElementById('annotation-form-overlay').classList.add('hidden');
    editId = null;
  }

  async function saveAnnotation() {
    const name = document.getElementById('annotation-name-input').value.trim();
    if (!name) { document.getElementById('annotation-name-input').focus(); return; }
    const notes = document.getElementById('annotation-notes-input').value.trim();
    try {
      if (editId != null) {
        await API.updateAnnotation(editId, { name, notes });
      } else {
        if (!pendingAnnotation) return;
        await API.createAnnotation({ ...pendingAnnotation, name, notes });
      }
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

  // Narrow the loaded window to the strip's current brush selection and re-fetch.
  // /api/points is size-aware decimated, so the same vertex budget over a smaller
  // window yields more detail — a quick "zoom in for granularity". Reads the live
  // brush (cf. useMarks, which reads the persisted marks).
  function zoomToRange() {
    const sel = TimeStrip.getSelection();
    if (!sel || sel.hiMs <= sel.loMs) return;
    TimePicker.setState({
      mode: 'range',
      from: new Date(sel.loMs),
      to: new Date(sel.hiMs),
      live: false,
    });
  }

  function init() {
    TimeStrip.init('tl-strip', 'tl-strip-tooltip');
    TimeStrip.onBrush(() => renderRange(false));

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

  return { init, getPoints, openEditAnnotation };
})();
