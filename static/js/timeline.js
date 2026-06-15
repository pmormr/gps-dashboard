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

  function pointsInRange(lo, hi) {
    return allPoints.filter(p => {
      const t = toTs(p.timestamp);
      return t >= lo && t <= hi;
    });
  }

  function updateSliderLabels(lo, hi) {
    document.getElementById('tl-start-label').textContent = sliderLabel(lo);
    document.getElementById('tl-end-label').textContent = sliderLabel(hi);
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
  }

  // Bucket size tier from the requested window. Keeps the returned point
  // count bounded so the polyline stays usable at week/month ranges; full
  // detail under a day.
  function bucketFor(spanMs) {
    const hour = 3600 * 1000;
    if (spanMs <= 24 * hour)      return null;
    if (spanMs <= 7 * 24 * hour)  return 30;
    if (spanMs <= 30 * 24 * hour) return 300;
    return 1800;
  }

  async function loadRange(range) {
    const { from, to, live } = range;
    if (!from || !to) return;

    // Treat live re-emits (anchor sliding forward) as continuations — don't
    // refit the map on every tick or the view jerks around.
    const isLiveTick = lastRange && lastRange.live && live &&
                       lastRange.mode === range.mode &&
                       lastRange.window === range.window;
    lastRange = range;

    const spanMs = to - from;
    const bucket = bucketFor(spanMs);
    const opts = bucket ? { bucket } : {};

    document.getElementById('tl-status').textContent = 'Loading…';
    document.getElementById('tl-empty').classList.add('hidden');

    let data;
    try {
      data = await API.getPoints(from.toISOString(), to.toISOString(), 20000, opts);
    } catch (e) {
      document.getElementById('tl-status').textContent = `Error: ${e.message}`;
      return;
    }

    allPoints = data.points;
    const bucketNote = bucket ? ` · ${bucket}s buckets` : '';
    document.getElementById('tl-status').textContent =
      `${allPoints.length.toLocaleString()} pts${data.truncated ? ' (truncated)' : ''}${bucketNote}`;

    if (!allPoints.length) {
      document.getElementById('tl-slider-wrap').classList.add('hidden');
      const emptyEl = document.getElementById('tl-empty');
      emptyEl.textContent = 'No GPS points for this range';
      emptyEl.classList.remove('hidden');
      MapView.clearTrack();
      if (slider) { slider.destroy(); slider = null; }
      return;
    }

    const lo = toTs(allPoints[0].timestamp);
    const hi = toTs(allPoints.at(-1).timestamp);
    const step = Math.max(1, Math.floor((hi - lo) / 1000));

    if (slider) { slider.destroy(); slider = null; }
    const el = document.getElementById('tl-slider');
    slider = noUiSlider.create(el, {
      start: [lo, hi],
      connect: true,
      range: { min: lo, max: hi === lo ? lo + 1 : hi },
      step,
    });
    slider.on('update', () => renderRange(false));
    document.getElementById('tl-slider-wrap').classList.remove('hidden');

    MapView.showTrack(allPoints, { fitBounds: !isLiveTick, showEndpoints: false });
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

  function openAnnotationForm() {
    if (!pendingAnnotation) return;
    const isPoint = !pendingAnnotation.end_time;
    document.getElementById('annotation-form-title').textContent =
      isPoint ? 'Drop Pin' : 'Create Range';
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

  // Drop Pin: captures the slider's hi handle (or "now" if live + no slider).
  // The form opens with end_time omitted so saveAnnotation creates a point.
  function dropPin() {
    let ts;
    if (slider) {
      const hi = slider.get().map(Number)[1];
      ts = fromTs(hi);
    } else {
      ts = new Date().toISOString();
    }
    pendingAnnotation = { start_time: ts };
    openAnnotationForm();
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

  function init() {
    document.getElementById('tl-create-btn').addEventListener('click', openAnnotationForm);
    document.getElementById('tl-drop-pin-btn').addEventListener('click', dropPin);
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
    document.getElementById('tl-zoom-here-btn').addEventListener('click', zoomToCurrentLocation);

    loadMarks();
    TimePicker.onChange(loadRange);
  }

  return { init, getPoints };
})();
