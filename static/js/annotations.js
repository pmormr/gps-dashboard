const Annotations = (() => {
  let annotations = [];
  let activeId = null;

  function renderList() {
    const el = document.getElementById('annotations-list');
    if (!annotations.length) {
      el.innerHTML = '<p class="empty-state">No annotations yet. Create one from the Timeline view.</p>';
      return;
    }

    el.innerHTML = annotations.map(a => `
      <div class="annotation-item ${a.id === activeId ? 'active' : ''}" data-id="${a.id}">
        <div class="annotation-item-main">
          <span class="annotation-name">${escHtml(a.name)}</span>
          <span class="annotation-meta">${fmtDate(a.start_time)} · ${a.point_count != null ? a.point_count.toLocaleString() + ' pts' : 'pin'}</span>
        </div>
        <button class="annotation-delete-btn" data-id="${a.id}" title="Delete annotation">×</button>
      </div>
    `).join('');

    el.querySelectorAll('.annotation-item').forEach(el => {
      el.addEventListener('click', e => {
        if (e.target.classList.contains('annotation-delete-btn')) return;
        selectAnnotation(parseInt(el.dataset.id));
      });
    });

    el.querySelectorAll('.annotation-delete-btn').forEach(btn => {
      btn.addEventListener('click', () => confirmDelete(parseInt(btn.dataset.id)));
    });
  }

  async function selectAnnotation(id) {
    activeId = id;
    const annotation = annotations.find(a => a.id === id);
    if (!annotation) return;

    renderList();
    showDetailView(annotation);

    if (!annotation.end_time) {
      // Point annotation: nothing to load yet; Phase 3 will pan/zoom the map
      // to the nearest gps_point. For now just show the meta panel.
      renderStats(annotation, [], false);
      return;
    }

    try {
      const data = await API.getPoints(annotation.start_time, annotation.end_time, 20000);
      AnnotationsMap.showTrack(data.points, { fitBounds: true, showEndpoints: true });
      renderStats(annotation, data.points, data.truncated);
    } catch (e) {
      const errEl = document.createElement('p');
      errEl.className = 'error';
      errEl.textContent = `Failed to load: ${e.message}`;
      document.getElementById('annotation-stats-panel').replaceChildren(errEl);
    }
  }

  function showDetailView(annotation) {
    document.getElementById('annotations-list-pane').classList.add('hidden-mobile');
    const detail = document.getElementById('annotations-detail-pane');
    detail.classList.remove('hidden');

    document.getElementById('detail-annotation-name').textContent = annotation.name;
    document.getElementById('annotation-stats-panel').innerHTML = '<p class="muted">Loading…</p>';
    AnnotationsMap.clearTrack();
    AnnotationsMap.invalidateSize();
  }

  function showListView() {
    activeId = null;
    document.getElementById('annotations-list-pane').classList.remove('hidden-mobile');
    document.getElementById('annotations-detail-pane').classList.add('hidden');
    AnnotationsMap.clearTrack();
    renderList();
  }

  function renderStats(annotation, points, truncated) {
    const panel = document.getElementById('annotation-stats-panel');

    if (!annotation.end_time) {
      panel.innerHTML = `
        <div class="annotation-dates">${fmtDate(annotation.start_time)} ${fmtTime(annotation.start_time)}</div>
        ${annotation.notes ? `<p class="annotation-notes">${escHtml(annotation.notes)}</p>` : ''}
      `;
      return;
    }

    const stats = computeStats(points);
    if (!stats) { panel.innerHTML = '<p class="muted">No data</p>'; return; }

    panel.innerHTML = `
      ${truncated ? '<p class="warning">Data truncated at 20,000 points — stats may be incomplete.</p>' : ''}
      <div class="stats-grid">
        <div class="stat"><span class="stat-val">${fmtDistance(stats.distance)}</span><span class="stat-lbl">Distance</span></div>
        <div class="stat"><span class="stat-val">${fmtDuration(stats.durationMs)}</span><span class="stat-lbl">Duration</span></div>
        <div class="stat"><span class="stat-val">${fmtSpeed(stats.maxSpeed)}</span><span class="stat-lbl">Max Speed</span></div>
        <div class="stat"><span class="stat-val">${fmtSpeed(stats.avgSpeed)}</span><span class="stat-lbl">Avg Speed</span></div>
        <div class="stat"><span class="stat-val">${fmtAltitude(stats.elevRange)}</span><span class="stat-lbl">Elev Range</span></div>
        <div class="stat"><span class="stat-val">${stats.pointCount.toLocaleString()}</span><span class="stat-lbl">Points</span></div>
      </div>
      <div class="annotation-dates">${fmtDate(annotation.start_time)} ${fmtTime(annotation.start_time)} → ${fmtTime(annotation.end_time)}</div>
      ${annotation.notes ? `<p class="annotation-notes">${escHtml(annotation.notes)}</p>` : ''}
    `;
  }

  async function confirmDelete(id) {
    const annotation = annotations.find(a => a.id === id);
    if (!annotation) return;
    if (!confirm(`Delete "${annotation.name}"?`)) return;
    try {
      await API.deleteAnnotation(id);
      if (activeId === id) showListView();
      await reload();
    } catch (e) {
      alert(`Delete failed: ${e.message}`);
    }
  }

  async function reload() {
    try {
      const data = await API.getAnnotations();
      annotations = data.annotations;
      renderList();
    } catch (e) {
      const errEl = document.createElement('p');
      errEl.className = 'error';
      errEl.textContent = `Failed to load annotations: ${e.message}`;
      document.getElementById('annotations-list').replaceChildren(errEl);
    }
  }

  function escHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function init() {
    document.getElementById('annotations-back-btn').addEventListener('click', showListView);
    reload();
  }

  return { init, reload };
})();
