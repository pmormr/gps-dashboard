const API = {
  async _fetch(url, options = {}) {
    const resp = await fetch(url, options);
    if (resp.status === 204) return null;
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(body.error || `HTTP ${resp.status}`);
    return body;
  },

  getPoints(start, end, limit = 5000) {
    const params = new URLSearchParams({ start, end, limit });
    return this._fetch(`/api/points?${params}`);
  },

  getPointsLatest() {
    return this._fetch('/api/points/latest');
  },

  getTrips() {
    return this._fetch('/api/trips');
  },

  createTrip(data) {
    return this._fetch('/api/trips', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  updateTrip(id, data) {
    return this._fetch(`/api/trips/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
  },

  deleteTrip(id) {
    return this._fetch(`/api/trips/${id}`, { method: 'DELETE' });
  },

  getMarks() {
    return this._fetch('/api/trips/mark');
  },

  markTimestamp(marker) {
    return this._fetch('/api/trips/mark', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ marker }),
    });
  },
};
