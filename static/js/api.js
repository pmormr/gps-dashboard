const API = {
  async _fetch(url, options = {}) {
    const resp = await fetch(url, options);
    if (resp.status === 204) return null;
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(body.error || `HTTP ${resp.status}`);
    return body;
  },

  getPoints(start, end, limit = 5000, opts = {}) {
    const params = new URLSearchParams({ start, end, limit });
    if (opts.bbox) params.set('bbox', opts.bbox);
    return this._fetch(`/api/points?${params}`);
  },

  getPointsLatest() {
    return this._fetch('/api/points/latest');
  },

  // Drone flights for the map overlay. No filters ⇒ every flight (the dataset is
  // tiny); pass start/end/bbox to scope, or points:false for a metadata listing.
  getDroneFlights(opts = {}) {
    const params = new URLSearchParams();
    if (opts.start) params.set('start', opts.start);
    if (opts.end) params.set('end', opts.end);
    if (opts.bbox) params.set('bbox', opts.bbox);
    if (opts.points === false) params.set('points', '0');
    const qs = params.toString();
    return this._fetch('/api/drone/flights' + (qs ? `?${qs}` : ''));
  },

  // Fuel economy over a window (derived OBD fuel ÷ GPS-track distance, O8).
  getObdEconomy(start, end) {
    const params = new URLSearchParams({ start, end });
    return this._fetch(`/api/obd/economy?${params}`);
  },

  getAnnotations() {
    return this._fetch('/api/annotations');
  },

  createAnnotation(data) {
    return this._fetch('/api/annotations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  updateAnnotation(id, data) {
    return this._fetch(`/api/annotations/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  deleteAnnotation(id) {
    return this._fetch(`/api/annotations/${id}`, { method: 'DELETE' });
  },

  getMarks() {
    return this._fetch('/api/annotations/mark');
  },

  markTimestamp(marker) {
    return this._fetch('/api/annotations/mark', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ marker }),
    });
  },
};
