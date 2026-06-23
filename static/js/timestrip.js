// TimeStrip — the map view's sub-range timeline, rendered entirely in one canvas
// (it replaced noUiSlider + the DOM overlay hacks in mapview-redesign S5). It owns
// a continuous wall-clock window domain and a two-handle brush selection, and
// draws every timeline layer in a single pass:
//   • point-density coverage — where data is / where it's genuinely empty (S4),
//   • stop dwell blocks — where you're parked (red, bottom lane),
//   • annotation range bands (cyan) + point ticks (amber) along the top lane,
//   • a dimmed mask over the unselected time, and the two drag handles.
// Pointer Events drive the brush (drag a handle to resize, drag the middle to pan,
// tap empty track to jump the nearest handle); arrow keys nudge it. Consumers:
// timeline.js (setData + getSelection + onBrush) and annotations.js
// (setAnnotations). All times are ms since epoch.
const TimeStrip = (() => {
  let canvas = null;
  let ctx = null;
  let tooltip = null;
  let domain = null;          // { startMs, endMs } — the loaded window
  let sel = null;             // { loMs, hiMs } — the brush
  let points = [];
  let annotations = [];
  let drag = null;            // { mode: 'lo'|'hi'|'pan', startX, startLo, startHi }
  const listeners = [];

  const HIT_PX = 20;                       // touch hit half-width around a handle
  const EDGE = 12;                         // inset so full-extent handles sit off
                                           // the canvas edge and stay grabbable
  const GAP_CAP_MS = 15 * 60 * 1000;       // density coverage gap cap (see S4)
  const PAD_TOP = 5;                       // top lane: annotation bands + ticks
  const PAD_BOT = 4;                       // bottom lane: stop dwell blocks

  // ── geometry ──────────────────────────────────────────────────────────────
  // Time maps onto the inset plot area [EDGE, cssW - EDGE]; the EDGE margins keep
  // the handles a finger's width inside the canvas even at full extent (the very
  // edge isn't reliably hit-testable).
  function cssW() { return canvas.clientWidth; }
  function cssH() { return canvas.clientHeight; }
  function plotW() { return Math.max(1, cssW() - 2 * EDGE); }
  function span() { return domain ? domain.endMs - domain.startMs : 0; }
  function xOfMs(ms) {
    const s = span();
    return s > 0 ? EDGE + ((ms - domain.startMs) / s) * plotW() : EDGE;
  }
  function msOfX(px) {
    return domain.startMs + ((px - EDGE) / plotW()) * span();
  }
  function clampMs(ms) {
    return Math.max(domain.startMs, Math.min(domain.endMs, ms));
  }

  function fmtDur(mins) {
    if (mins < 60) return `${mins}m`;
    const h = Math.floor(mins / 60);
    if (h < 24) { const m = mins % 60; return m ? `${h}h ${m}m` : `${h}h`; }
    const d = Math.floor(h / 24);
    const rh = h % 24;
    return rh ? `${d}d ${rh}h` : `${d}d`;
  }

  // ── public API ──────────────────────────────────────────────────────────────
  function init(canvasId, tooltipId) {
    canvas = document.getElementById(canvasId);
    if (!canvas) return;
    ctx = canvas.getContext('2d');
    tooltip = tooltipId ? document.getElementById(tooltipId) : null;
    canvas.tabIndex = 0;
    canvas.setAttribute('role', 'slider');
    canvas.setAttribute('aria-label', 'Time selection');
    canvas.addEventListener('pointerdown', onPointerDown);
    canvas.addEventListener('pointermove', onPointerMove);
    canvas.addEventListener('pointerup', onPointerUp);
    canvas.addEventListener('pointercancel', onPointerUp);
    canvas.addEventListener('pointerleave', hideTooltip);
    canvas.addEventListener('keydown', onKeyDown);
    window.addEventListener('resize', draw);
  }

  // Load a new window: reset the brush to the full window (matching the old
  // noUiSlider re-create-on-load behavior) and redraw. Does not emit — the
  // caller drives the first renderRange so it can control fitBounds.
  function setData({ startMs, endMs, points: pts, annotations: anns }) {
    domain = { startMs, endMs };
    points = pts || [];
    if (anns !== undefined) annotations = anns || [];
    sel = { loMs: startMs, hiMs: endMs };
    draw();
  }

  function setAnnotations(anns) {
    annotations = anns || [];
    draw();
  }

  function getSelection() {
    return sel ? { loMs: sel.loMs, hiMs: sel.hiMs } : null;
  }

  function onBrush(fn) { listeners.push(fn); }
  function emit() { listeners.forEach(fn => fn()); }

  // ── drawing ───────────────────────────────────────────────────────────────
  function draw() {
    if (!canvas || !ctx) return;
    const w = cssW(), h = cssH();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!domain || !sel || span() <= 0 || w < 1) return;
    drawDensity(w, h);
    drawStops(w, h);
    drawAnnotations(w, h);
    drawSelection(w, h);
    drawHandles(w, h);
  }

  // Per-pixel-column coverage fill, density-modulated (mapview-redesign S4):
  // stops fill their dwell interval, moving vertices raise the column's density
  // and bridge the gap to the previous vertex within GAP_CAP_MS so a drive reads
  // solid (importance-thinning drops collinear points) while a real outage stays
  // empty. Bar height/alpha scale sqrt(count): a drive reads bright, a park a low
  // floor, van-off the bare track.
  function drawDensity(w, h) {
    const cols = Math.max(1, Math.floor(w));
    const counts = new Float64Array(cols);
    const covered = new Uint8Array(cols);
    // Bin by pixel column via xOfMs, so the coverage respects the EDGE inset.
    const colOf = (ms) => Math.min(cols - 1, Math.max(0, Math.round(xOfMs(ms))));
    const fillCols = (loMs, hiMs) => {
      const a = colOf(Math.max(loMs, domain.startMs));
      const b = colOf(Math.min(hiMs, domain.endMs));
      for (let c = a; c <= b; c++) covered[c] = 1;
    };
    let prev = null;
    for (const p of points) {
      if (p.kind === 'stop' && p.dwell_start && p.dwell_end) {
        fillCols(new Date(p.dwell_start).getTime(), new Date(p.dwell_end).getTime());
        prev = null;
        continue;
      }
      const ms = new Date(p.timestamp).getTime();
      if (ms < domain.startMs || ms > domain.endMs) { prev = null; continue; }
      const c = colOf(ms);
      counts[c] += 1;
      covered[c] = 1;
      if (prev != null && ms - prev <= GAP_CAP_MS) fillCols(prev, ms);
      prev = ms;
    }
    let maxC = 0;
    for (let c = 0; c < cols; c++) if (counts[c] > maxC) maxC = counts[c];
    const bodyBot = h - PAD_BOT;
    const bodyH = bodyBot - PAD_TOP;
    const floor = 0.4;
    for (let c = 0; c < cols; c++) {
      if (!covered[c]) continue;
      const dens = maxC > 0 ? Math.sqrt(counts[c] / maxC) : 0;
      const barH = Math.max(2, (floor + (1 - floor) * dens) * bodyH);
      ctx.fillStyle = `rgba(148, 163, 184, ${0.5 + 0.45 * dens})`;
      ctx.fillRect(c, bodyBot - barH, 1, barH);
    }
  }

  function drawStops(w, h) {
    ctx.fillStyle = 'rgba(239, 68, 68, 0.85)';
    for (const p of points) {
      if (p.kind !== 'stop' || !p.dwell_start || !p.dwell_end) continue;
      const sMs = new Date(p.dwell_start).getTime();
      const eMs = new Date(p.dwell_end).getTime();
      if (eMs < domain.startMs || sMs > domain.endMs) continue;
      const x0 = xOfMs(Math.max(sMs, domain.startMs));
      const x1 = xOfMs(Math.min(eMs, domain.endMs));
      ctx.fillRect(x0, h - PAD_BOT, Math.max(1.5, x1 - x0), PAD_BOT);
    }
  }

  function drawAnnotations(w, h) {
    for (const a of annotations) {
      const sMs = new Date(a.start_time).getTime();
      if (a.end_time) {
        const eMs = new Date(a.end_time).getTime();
        if (eMs < domain.startMs || sMs > domain.endMs) continue;
        const x0 = xOfMs(Math.max(sMs, domain.startMs));
        const x1 = xOfMs(Math.min(eMs, domain.endMs));
        ctx.fillStyle = 'rgba(34, 211, 238, 0.65)';
        ctx.fillRect(x0, 0, Math.max(1.5, x1 - x0), 3);
      } else {
        if (sMs < domain.startMs || sMs > domain.endMs) continue;
        ctx.fillStyle = '#f59e0b';
        ctx.fillRect(xOfMs(sMs) - 1, 0, 2, PAD_TOP + 4);
      }
    }
  }

  // Dim the unselected time so the brushed window pops, plus a primary-colored
  // top/bottom edge on the selection.
  function drawSelection(w, h) {
    const xLo = xOfMs(sel.loMs);
    const xHi = xOfMs(sel.hiMs);
    const x0 = EDGE;
    const x1 = w - EDGE;
    ctx.fillStyle = 'rgba(15, 23, 42, 0.55)';
    if (xLo > x0) ctx.fillRect(x0, 0, xLo - x0, h);
    if (xHi < x1) ctx.fillRect(xHi, 0, x1 - xHi, h);
    ctx.fillStyle = 'rgba(59, 130, 246, 0.9)';
    const selW = Math.max(1, xHi - xLo);
    ctx.fillRect(xLo, 0, selW, 1.5);
    ctx.fillRect(xLo, h - 1.5, selW, 1.5);
  }

  function drawHandles(w, h) {
    const knobW = 8, knobH = 14, knobY = (h - knobH) / 2;
    for (const ms of [sel.loMs, sel.hiMs]) {
      const x = xOfMs(ms);
      ctx.fillStyle = '#3b82f6';
      ctx.fillRect(x - 1, 0, 2, h);
      roundRect(ctx, x - knobW / 2, knobY, knobW, knobH, 3);
      ctx.fillStyle = '#3b82f6';
      ctx.fill();
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }

  function roundRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }

  // ── interaction ─────────────────────────────────────────────────────────────
  function localX(e) {
    return e.clientX - canvas.getBoundingClientRect().left;
  }

  // Which grab target is at px: a handle (within HIT_PX), the connect region
  // between the handles (pan), or null (empty track).
  function targetAtX(px) {
    const xLo = xOfMs(sel.loMs);
    const xHi = xOfMs(sel.hiMs);
    const dLo = Math.abs(px - xLo);
    const dHi = Math.abs(px - xHi);
    if (dLo <= HIT_PX && dLo <= dHi) return 'lo';
    if (dHi <= HIT_PX) return 'hi';
    if (px > xLo && px < xHi) return 'pan';
    return null;
  }

  function onPointerDown(e) {
    if (!domain || !sel) return;
    const px = localX(e);
    let mode = targetAtX(px);
    if (mode === null) {
      // Tap empty track: jump the nearest handle here, then drag it.
      const ms = clampMs(msOfX(px));
      mode = Math.abs(px - xOfMs(sel.loMs)) <= Math.abs(px - xOfMs(sel.hiMs)) ? 'lo' : 'hi';
      if (mode === 'lo') sel.loMs = Math.min(ms, sel.hiMs);
      else sel.hiMs = Math.max(ms, sel.loMs);
      draw();
      emit();
    }
    drag = { mode, startX: px, startLo: sel.loMs, startHi: sel.hiMs };
    try { canvas.setPointerCapture(e.pointerId); } catch (_) {}
    hideTooltip();
    e.preventDefault();
  }

  function onPointerMove(e) {
    if (!domain || !sel) return;
    const px = localX(e);
    if (!drag) { updateHover(px); return; }
    const ms = clampMs(msOfX(px));
    if (drag.mode === 'lo') {
      sel.loMs = Math.min(ms, sel.hiMs);
    } else if (drag.mode === 'hi') {
      sel.hiMs = Math.max(ms, sel.loMs);
    } else {
      const dMs = msOfX(px) - msOfX(drag.startX);
      const width = drag.startHi - drag.startLo;
      let lo = drag.startLo + dMs;
      let hi = drag.startHi + dMs;
      if (lo < domain.startMs) { lo = domain.startMs; hi = lo + width; }
      if (hi > domain.endMs) { hi = domain.endMs; lo = hi - width; }
      sel.loMs = lo;
      sel.hiMs = hi;
    }
    draw();
    emit();
    e.preventDefault();
  }

  function onPointerUp(e) {
    if (!drag) return;
    drag = null;
    try { canvas.releasePointerCapture(e.pointerId); } catch (_) {}
  }

  function updateHover(px) {
    const mode = targetAtX(px);
    canvas.style.cursor =
      mode === 'lo' || mode === 'hi' ? 'ew-resize' : mode === 'pan' ? 'grab' : 'crosshair';
    const ms = msOfX(px);
    let text = null;
    for (const p of points) {
      if (p.kind !== 'stop' || !p.dwell_start || !p.dwell_end) continue;
      const sMs = new Date(p.dwell_start).getTime();
      const eMs = new Date(p.dwell_end).getTime();
      if (ms >= sMs && ms <= eMs) { text = 'Parked ' + fmtDur(Math.round((eMs - sMs) / 60000)); break; }
    }
    if (!text) {
      for (const a of annotations) {
        const sMs = new Date(a.start_time).getTime();
        if (a.end_time) {
          if (ms >= sMs && ms <= new Date(a.end_time).getTime()) { text = a.name; break; }
        } else if (Math.abs(xOfMs(sMs) - px) <= 4) { text = a.name; break; }
      }
    }
    if (text) showTooltip(text, px); else hideTooltip();
  }

  function showTooltip(text, px) {
    if (!tooltip) return;
    tooltip.textContent = text;
    tooltip.style.left = px + 'px';
    tooltip.classList.remove('hidden');
  }

  function hideTooltip() {
    if (tooltip) tooltip.classList.add('hidden');
  }

  function onKeyDown(e) {
    if (!domain || !sel) return;
    const step = span() * 0.02;
    const width = sel.hiMs - sel.loMs;
    let handled = true;
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      const dir = e.key === 'ArrowRight' ? 1 : -1;
      if (e.shiftKey) {
        sel.hiMs = Math.max(sel.loMs, clampMs(sel.hiMs + dir * step));
      } else {
        let lo = sel.loMs + dir * step;
        let hi = sel.hiMs + dir * step;
        if (lo < domain.startMs) { lo = domain.startMs; hi = lo + width; }
        if (hi > domain.endMs) { hi = domain.endMs; lo = hi - width; }
        sel.loMs = lo;
        sel.hiMs = hi;
      }
    } else if (e.key === 'Home') {
      sel.loMs = domain.startMs;
      sel.hiMs = domain.startMs + width;
    } else if (e.key === 'End') {
      sel.hiMs = domain.endMs;
      sel.loMs = domain.endMs - width;
    } else {
      handled = false;
    }
    if (handled) { draw(); emit(); e.preventDefault(); }
  }

  return { init, setData, setAnnotations, getSelection, onBrush };
})();
