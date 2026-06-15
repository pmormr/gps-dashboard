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
    if (opts.bucket) params.set('bucket', opts.bucket);
    if (opts.bbox)   params.set('bbox', opts.bbox);
    return this._fetch(`/api/points?${params}`);
  },

  getPointsLatest() {
    return this._fetch('/api/points/latest');
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
