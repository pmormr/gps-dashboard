const Annotations = (() => {
  let annotations = [];
  let pendingPanTimestamp = null;   // set when jumping to a point annotation

  function escHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function fmtRangeBounds(ann) {
    const start = fmtDate(ann.start_time);
    if (!ann.end_time) return start;
    return `${start} · ${fmtTime(ann.start_time)} → ${fmtTime(ann.end_time)}`;
  }

  function renderList() {
    const el = document.getElementById('annotations-list');
    if (!annotations.length) {
      el.innerHTML = '<p class="empty-state">No annotations yet. Mark a range on the slider and Create Range, or Drop Pin for a single moment.</p>';
      return;
    }

    el.innerHTML = annotations.map(a => {
      const isPoint = !a.end_time;
      const typeIcon = isPoint ? '📍' : '⇄';
      const meta = isPoint
        ? `${fmtDate(a.start_time)} · ${fmtTime(a.start_time)}`
        : `${fmtRangeBounds(a)} · ${a.point_count != null ? a.point_count.toLocaleString() + ' pts' : ''}`;
      return `
        <div class="annotation-item" data-id="${a.id}">
          <span class="annotation-type">${typeIcon}</span>
          <div class="annotation-item-main">
            <span class="annotation-name">${escHtml(a.name)}</span>
            <span class="annotation-meta">${escHtml(meta)}</span>
            ${a.notes ? `<span class="annotation-notes">${escHtml(a.notes)}</span>` : ''}
            ${!isPoint ? `<span class="annotation-econ" data-id="${a.id}"></span>` : ''}
          </div>
          <button class="annotation-delete-btn" data-id="${a.id}" title="Delete">×</button>
        </div>
      `;
    }).join('');

    el.querySelectorAll('.annotation-item').forEach(item => {
      item.addEventListener('click', e => {
        if (e.target.classList.contains('annotation-delete-btn')) return;
        jumpTo(parseInt(item.dataset.id, 10));
      });
    });
    el.querySelectorAll('.annotation-delete-btn').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        confirmDelete(parseInt(btn.dataset.id, 10));
      });
    });

    loadEconomies();
  }

  // Lazy-fill each range annotation's fuel economy (derived OBD fuel ÷ GPS
  // distance). Skips ranges with no OBD coverage (e.g. annotations predating the
  // OBD reader, or no engine-on data in the window). Fire-and-forget per item so
  // a slow/empty fetch never blocks the list render.
  function loadEconomies() {
    for (const a of annotations.filter(x => x.end_time)) {
      const span = document.querySelector(`.annotation-econ[data-id="${a.id}"]`);
      if (!span) continue;
      API.getObdEconomy(a.start_time, a.end_time).then(e => {
        if (!e || !e.n_obd_rows || e.mpg == null) return;
        span.textContent =
          `⛽ ${e.mpg.toFixed(1)} mpg · ${e.distance_mi.toFixed(1)} mi · ${e.fuel_gallons.toFixed(2)} gal`;
      }).catch(() => { /* leave blank on error */ });
    }
  }

  function updateDrawerCount() {
    const badge = document.getElementById('annotations-drawer-count');
    if (!badge) return;
    if (!annotations.length) {
      badge.classList.add('hidden');
    } else {
      badge.textContent = annotations.length;
      badge.classList.remove('hidden');
    }
  }

  // Click an annotation → set the picker so the loaded window frames it.
  // Range: explicit (from, to). Point: keep current window size, center on
  // its timestamp (`around` mode), turn off Live. After the resulting
  // loadRange completes, Timeline.afterLoad pans the map to the nearest fix.
  function jumpTo(id) {
    const ann = annotations.find(a => a.id === id);
    if (!ann) return;

    if (ann.end_time) {
      pendingPanTimestamp = null;
      TimePicker.setState({
        mode: 'range',
        from: new Date(ann.start_time),
        to: new Date(ann.end_time),
        live: false,
      });
    } else {
      const ts = new Date(ann.start_time);
      pendingPanTimestamp = ts;
      const cur = TimePicker.getRange();
      const windowMs = cur.window || 24 * 3600 * 1000;
      TimePicker.setState({
        mode: 'around',
        anchor: ts,
        window: windowMs,
        live: false,
      });
    }
  }

  async function confirmDelete(id) {
    const a = annotations.find(x => x.id === id);
    if (!a) return;
    if (!confirm(`Delete "${a.name}"?`)) return;
    try {
      await API.deleteAnnotation(id);
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
      updateDrawerCount();
      // Set of annotations changed → re-render overlays against the points
      // Timeline already has loaded (no extra fetch).
      if (typeof Timeline !== 'undefined' && Timeline.getPoints) {
        renderOverlays(Timeline.getPoints());
      }
    } catch (e) {
      const el = document.getElementById('annotations-list');
      el.innerHTML = `<p class="error">Failed to load: ${escHtml(e.message)}</p>`;
    }
  }

  // Called by Timeline after each loadRange completes. Renders annotations
  // on the map (pins for points, highlighted segments for ranges) and on
  // the slider (bands + ticks), constrained to the currently loaded window.
  // Filled in by item 3.2.
  function renderOverlays(loadedPoints) {
    MapView.clearAnnotations();
    renderSliderOverlay(null, null);
    if (!loadedPoints || !loadedPoints.length) return;

    const winStart = loadedPoints[0].timestamp;
    const winEnd = loadedPoints.at(-1).timestamp;
    const winStartMs = new Date(winStart).getTime();
    const winEndMs = new Date(winEnd).getTime();

    for (const a of annotations) {
      if (a.end_time) {
        if (a.start_time > winEnd || a.end_time < winStart) continue;
        const segment = loadedPoints.filter(p =>
          p.timestamp >= a.start_time && p.timestamp <= a.end_time
        );
        if (segment.length >= 2) MapView.addRangeOverlay(segment, a.name);
      } else {
        if (a.start_time < winStart || a.start_time > winEnd) continue;
        const nearest = findNearestByTime(loadedPoints, new Date(a.start_time));
        if (nearest) MapView.addPinOverlay(nearest.lat, nearest.lon, a.name);
      }
    }

    renderSliderOverlay(winStartMs, winEndMs);

    // Pan to a point annotation that was just clicked, now that points have
    // landed for the new window.
    if (pendingPanTimestamp) {
      const nearest = findNearestByTime(loadedPoints, pendingPanTimestamp);
      pendingPanTimestamp = null;
      if (nearest) MapView.zoomTo(nearest.lat, nearest.lon, 14);
    }
  }

  // Slider overlay: shaded bands for ranges in window, ticks for points.
  // The slider's noUiSlider track is `display: block`; we draw inside an
  // absolutely-positioned sibling div (#tl-slider-overlay) over the track.
  function renderSliderOverlay(winStartMs, winEndMs) {
    const overlay = document.getElementById('tl-slider-overlay');
    if (!overlay) return;
    overlay.innerHTML = '';
    if (winStartMs == null || winEndMs == null || winEndMs <= winStartMs) return;
    const span = winEndMs - winStartMs;

    for (const a of annotations) {
      const startMs = new Date(a.start_time).getTime();
      if (a.end_time) {
        const endMs = new Date(a.end_time).getTime();
        if (endMs < winStartMs || startMs > winEndMs) continue;
        const lo = Math.max(startMs, winStartMs);
        const hi = Math.min(endMs, winEndMs);
        const left = ((lo - winStartMs) / span) * 100;
        const width = ((hi - lo) / span) * 100;
        const band = document.createElement('div');
        band.className = 'sl-ann-band';
        band.style.left = left + '%';
        band.style.width = width + '%';
        band.title = a.name;
        overlay.appendChild(band);
      } else {
        if (startMs < winStartMs || startMs > winEndMs) continue;
        const left = ((startMs - winStartMs) / span) * 100;
        const tick = document.createElement('div');
        tick.className = 'sl-ann-tick';
        tick.style.left = left + '%';
        tick.title = a.name;
        overlay.appendChild(tick);
      }
    }
  }

  function findNearestByTime(points, when) {
    if (!points.length) return null;
    const targetMs = +when;
    let best = points[0];
    let bestDiff = Math.abs(new Date(best.timestamp) - targetMs);
    for (const p of points) {
      const d = Math.abs(new Date(p.timestamp) - targetMs);
      if (d < bestDiff) { best = p; bestDiff = d; }
    }
    return best;
  }

  function init() {
    reload();
  }

  return { init, reload, renderOverlays };
})();
