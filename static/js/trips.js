const Trips = (() => {
  let trips = [];
  let activeId = null;

  function renderList() {
    const el = document.getElementById('trips-list');
    if (!trips.length) {
      el.innerHTML = '<p class="empty-state">No trips yet. Create one from the Timeline view.</p>';
      return;
    }

    el.innerHTML = trips.map(t => `
      <div class="trip-item ${t.id === activeId ? 'active' : ''}" data-id="${t.id}">
        <div class="trip-item-main">
          <span class="trip-name">${escHtml(t.name)}</span>
          <span class="trip-meta">${fmtDate(t.start_time)} · ${t.point_count.toLocaleString()} pts</span>
        </div>
        <button class="trip-delete-btn" data-id="${t.id}" title="Delete trip">×</button>
      </div>
    `).join('');

    el.querySelectorAll('.trip-item').forEach(el => {
      el.addEventListener('click', e => {
        if (e.target.classList.contains('trip-delete-btn')) return;
        selectTrip(parseInt(el.dataset.id));
      });
    });

    el.querySelectorAll('.trip-delete-btn').forEach(btn => {
      btn.addEventListener('click', () => confirmDelete(parseInt(btn.dataset.id)));
    });
  }

  async function selectTrip(id) {
    activeId = id;
    const trip = trips.find(t => t.id === id);
    if (!trip) return;

    renderList();
    showDetailView(trip);

    try {
      const data = await API.getPoints(trip.start_time, trip.end_time, 20000);
      TripsMap.showTrack(data.points, { fitBounds: true, showEndpoints: true });
      renderStats(trip, data.points);
    } catch (e) {
      document.getElementById('trip-stats-panel').innerHTML = `<p class="error">Failed to load: ${e.message}</p>`;
    }
  }

  function showDetailView(trip) {
    document.getElementById('trips-list-pane').classList.add('hidden-mobile');
    const detail = document.getElementById('trips-detail-pane');
    detail.classList.remove('hidden');

    document.getElementById('detail-trip-name').textContent = trip.name;
    document.getElementById('trip-stats-panel').innerHTML = '<p class="muted">Loading…</p>';
    TripsMap.clearTrack();
    TripsMap.invalidateSize();
  }

  function showListView() {
    activeId = null;
    document.getElementById('trips-list-pane').classList.remove('hidden-mobile');
    document.getElementById('trips-detail-pane').classList.add('hidden');
    TripsMap.clearTrack();
    renderList();
  }

  function renderStats(trip, points) {
    const stats = computeStats(points);
    const panel = document.getElementById('trip-stats-panel');
    if (!stats) { panel.innerHTML = '<p class="muted">No data</p>'; return; }

    panel.innerHTML = `
      <div class="stats-grid">
        <div class="stat"><span class="stat-val">${fmtDistance(stats.distance)}</span><span class="stat-lbl">Distance</span></div>
        <div class="stat"><span class="stat-val">${fmtDuration(stats.durationMs)}</span><span class="stat-lbl">Duration</span></div>
        <div class="stat"><span class="stat-val">${fmtSpeed(stats.maxSpeed)}</span><span class="stat-lbl">Max Speed</span></div>
        <div class="stat"><span class="stat-val">${fmtSpeed(stats.avgSpeed)}</span><span class="stat-lbl">Avg Speed</span></div>
        <div class="stat"><span class="stat-val">${fmtAltitude(stats.elevGain)}</span><span class="stat-lbl">Elev Gain</span></div>
        <div class="stat"><span class="stat-val">${stats.pointCount.toLocaleString()}</span><span class="stat-lbl">Points</span></div>
      </div>
      <div class="trip-dates">${fmtDate(trip.start_time)} ${fmtTime(trip.start_time)} → ${fmtTime(trip.end_time)}</div>
      ${trip.notes ? `<p class="trip-notes">${escHtml(trip.notes)}</p>` : ''}
    `;
  }

  async function confirmDelete(id) {
    const trip = trips.find(t => t.id === id);
    if (!trip) return;
    if (!confirm(`Delete "${trip.name}"?`)) return;
    try {
      await API.deleteTrip(id);
      if (activeId === id) showListView();
      await reload();
    } catch (e) {
      alert(`Delete failed: ${e.message}`);
    }
  }

  async function reload() {
    try {
      const data = await API.getTrips();
      trips = data.trips;
      renderList();
    } catch (e) {
      document.getElementById('trips-list').innerHTML = `<p class="error">Failed to load trips: ${e.message}</p>`;
    }
  }

  function escHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function init() {
    document.getElementById('trips-back-btn').addEventListener('click', showListView);
    reload();
  }

  return { init, reload };
})();
