const Timeline = (() => {
  let allPoints = [];
  let slider = null;
  let pendingTrip = null;

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

  function renderRange() {
    if (!slider) return;
    const [lo, hi] = slider.get().map(Number);
    updateSliderLabels(lo, hi);
    const pts = pointsInRange(lo, hi);
    MapView.showTrack(pts, { fitBounds: false, showEndpoints: pts.length > 1 });
    document.getElementById('tl-selection-count').textContent = `${pts.length} points selected`;

    pendingTrip = pts.length >= 2
      ? { start_time: fromTs(lo), end_time: fromTs(hi) }
      : null;
    document.getElementById('tl-create-btn').disabled = !pendingTrip;
  }

  async function loadDate(dateStr) {
    const start = `${dateStr}T00:00:00Z`;
    const end   = `${dateStr}T23:59:59Z`;

    document.getElementById('tl-status').textContent = 'Loading…';
    document.getElementById('tl-slider-wrap').classList.add('hidden');
    document.getElementById('tl-empty').classList.add('hidden');
    MapView.clearTrack();

    try {
      const data = await API.getPoints(start, end, 20000);
      allPoints = data.points;
    } catch (e) {
      document.getElementById('tl-status').textContent = `Error: ${e.message}`;
      return;
    }

    if (!allPoints.length) {
      document.getElementById('tl-status').textContent = '';
      document.getElementById('tl-empty').classList.remove('hidden');
      return;
    }

    document.getElementById('tl-status').textContent = `${allPoints.length} points`;

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

    slider.on('update', () => renderRange());
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

    loadDate(today);
  }

  return { init };
})();
