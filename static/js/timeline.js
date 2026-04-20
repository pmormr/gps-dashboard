const Timeline = (() => {
  let allPoints = [];
  let slider = null;
  let pendingTrip = null;
  let currentMarks = {};
  let liveMode = false;
  let refreshInterval = null;

  function toTs(isoString) { return Math.floor(new Date(isoString).getTime() / 1000); }
  function fromTs(sec) { return new Date(sec * 1000).toISOString(); }

  function sliderLabel(sec) {
    return new Date(sec * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
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

  function isToday(dateStr) {
    return dateStr === new Date().toISOString().slice(0, 10);
  }

  function renderRange(followMap = false) {
    if (!slider) return;
    const [lo, hi] = slider.get().map(Number);
    updateSliderLabels(lo, hi);
    const pts = pointsInRange(lo, hi);
    MapView.showTrack(pts, { fitBounds: followMap, showEndpoints: pts.length > 1 });
    document.getElementById('tl-selection-count').textContent = `${pts.length} points selected`;

    pendingTrip = pts.length >= 2
      ? { start_time: fromTs(lo), end_time: fromTs(hi) }
      : null;
    document.getElementById('tl-create-btn').disabled = !pendingTrip;
  }

  function updateLiveBtn() {
    const btn = document.getElementById('tl-live-btn');
    if (!btn) return;
    btn.classList.toggle('btn-live-active', liveMode);
    btn.textContent = liveMode ? 'Live ●' : 'Live';
  }

  function stopLive() {
    liveMode = false;
    if (refreshInterval) { clearInterval(refreshInterval); refreshInterval = null; }
    updateLiveBtn();
  }

  async function refreshPoints() {
    const dateInput = document.getElementById('timeline-date');
    if (!isToday(dateInput.value)) { stopLive(); return; }

    const dateStr = dateInput.value;
    const start = `${dateStr}T00:00:00Z`;
    const end   = `${dateStr}T23:59:59Z`;

    let data;
    try {
      data = await API.getPoints(start, end, 20000);
    } catch (_) { return; }

    if (!data.points.length) return;

    const hadPoints = allPoints.length > 0;
    allPoints = data.points;

    const newMax = toTs(allPoints.at(-1).timestamp);
    const newMin = toTs(allPoints[0].timestamp);

    if (!slider) return;

    const [currentLo] = slider.get().map(Number);
    slider.updateOptions({ range: { min: newMin, max: newMax } }, false);

    if (liveMode) {
      slider.set([currentLo, newMax]);
      renderRange(true);
    }

    document.getElementById('tl-status').textContent =
      data.truncated ? `${allPoints.length} points (truncated)` : `${allPoints.length} points`;
  }

  function startLive() {
    const dateInput = document.getElementById('timeline-date');
    if (!isToday(dateInput.value)) return;

    liveMode = true;
    updateLiveBtn();

    if (!refreshInterval) {
      refreshInterval = setInterval(refreshPoints, 30000);
    }
    refreshPoints();
  }

  function toggleLive() {
    if (liveMode) {
      stopLive();
    } else {
      startLive();
    }
  }

  async function zoomToCurrentLocation() {
    try {
      const pt = await API.getPointsLatest();
      if (pt && pt.lat != null && pt.lon != null) {
        MapView.zoomTo(pt.lat, pt.lon, 17);
      }
    } catch (_) {}
  }

  async function loadDate(dateStr) {
    stopLive();

    const start = `${dateStr}T00:00:00Z`;
    const end   = `${dateStr}T23:59:59Z`;

    document.getElementById('tl-status').textContent = 'Loading…';
    document.getElementById('tl-slider-wrap').classList.add('hidden');
    document.getElementById('tl-empty').classList.add('hidden');
    MapView.clearTrack();

    const liveBtn = document.getElementById('tl-live-btn');
    if (liveBtn) liveBtn.disabled = !isToday(dateStr);

    let truncated = false;
    try {
      const data = await API.getPoints(start, end, 20000);
      allPoints = data.points;
      truncated = !!data.truncated;
    } catch (e) {
      document.getElementById('tl-status').textContent = `Error: ${e.message}`;
      return;
    }

    if (!allPoints.length) {
      document.getElementById('tl-status').textContent = '';
      const emptyEl = document.getElementById('tl-empty');
      emptyEl.textContent = 'No GPS points for this date';
      emptyEl.classList.remove('hidden');
      return;
    }

    document.getElementById('tl-status').textContent = truncated
      ? `${allPoints.length} points (truncated)`
      : `${allPoints.length} points`;

    const lo = toTs(allPoints[0].timestamp);
    const hi = toTs(allPoints.at(-1).timestamp);

    if (slider) { slider.destroy(); slider = null; }

    const el = document.getElementById('tl-slider');
    slider = noUiSlider.create(el, {
      start: [lo, hi],
      connect: true,
      range: { min: lo, max: hi === lo ? lo + 1 : hi },
      step: 30,
    });

    slider.on('update', () => renderRange(false));
    document.getElementById('tl-slider-wrap').classList.remove('hidden');
    MapView.showTrack(allPoints, { fitBounds: true, showEndpoints: false });
  }

  function openTripForm() {
    if (!pendingTrip) return;
    document.getElementById('trip-name-input').value = '';
    document.getElementById('trip-notes-input').value = '';
    document.getElementById('trip-form-overlay').classList.remove('hidden');
    document.getElementById('trip-name-input').focus();
  }

  function closeTripForm() {
    document.getElementById('trip-form-overlay').classList.add('hidden');
  }

  async function saveTrip() {
    const name = document.getElementById('trip-name-input').value.trim();
    if (!name) { document.getElementById('trip-name-input').focus(); return; }
    if (!pendingTrip) return;

    const notes = document.getElementById('trip-notes-input').value.trim();
    try {
      await API.createTrip({ ...pendingTrip, name, notes });
      closeTripForm();
      Trips.reload();
    } catch (e) {
      alert(`Failed to save trip: ${e.message}`);
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
    if (s || e) {
      statusEl.textContent = `S: ${fmtMarkTime(s)}  E: ${fmtMarkTime(e)}`;
    } else {
      statusEl.textContent = '';
    }
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

  async function useMarks() {
    const s = currentMarks.start;
    const e = currentMarks.end;
    if (!s || !e) return;

    const markDate = s.slice(0, 10);
    const dateInput = document.getElementById('timeline-date');

    if (dateInput.value !== markDate) {
      dateInput.value = markDate;
      await loadDate(markDate);
    }

    if (!slider) return;
    const lo = toTs(s);
    const hi = toTs(e);
    slider.set([lo, hi]);
  }

  function init() {
    const dateInput = document.getElementById('timeline-date');
    const today = new Date().toISOString().slice(0, 10);
    dateInput.value = today;
    dateInput.addEventListener('change', e => loadDate(e.target.value));

    document.getElementById('tl-create-btn').addEventListener('click', openTripForm);
    document.getElementById('trip-form-cancel').addEventListener('click', closeTripForm);
    document.getElementById('trip-form-save').addEventListener('click', saveTrip);
    document.getElementById('trip-form-overlay').addEventListener('click', e => {
      if (e.target === e.currentTarget) closeTripForm();
    });
    document.getElementById('trip-name-input').addEventListener('keydown', e => {
      if (e.key === 'Enter') saveTrip();
      if (e.key === 'Escape') closeTripForm();
    });

    document.getElementById('tl-mark-start-btn').addEventListener('click', () => handleMark('start'));
    document.getElementById('tl-mark-end-btn').addEventListener('click', () => handleMark('end'));
    document.getElementById('tl-use-marks-btn').addEventListener('click', useMarks);

    document.getElementById('tl-live-btn').addEventListener('click', toggleLive);
    document.getElementById('tl-zoom-here-btn').addEventListener('click', zoomToCurrentLocation);

    loadMarks();
    loadDate(today);
  }

  return { init };
})();
