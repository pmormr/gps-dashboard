function haversineMeters(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const φ1 = lat1 * Math.PI / 180, φ2 = lat2 * Math.PI / 180;
  const Δφ = (lat2 - lat1) * Math.PI / 180;
  const Δλ = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(Δφ / 2) ** 2 + Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

// gpsd reports Doppler-derived ground speed, which is reliable even at low speed
// unlike differencing the noisy position. Below this, the receiver is parked and
// its position wander is GPS jitter, not travel — so those segments are excluded
// from distance to stop a parked stop from inflating the total.
const SPEED_GATE_MPS = 0.5;

function computeStats(points) {
  if (!points.length) return null;

  let distance = 0;
  let elevMin = null, elevMax = null;
  const speeds = [];

  for (let i = 0; i < points.length; i++) {
    const alt = points[i].altitude;
    if (alt != null) {
      if (elevMin == null || alt < elevMin) elevMin = alt;
      if (elevMax == null || alt > elevMax) elevMax = alt;
    }
    // Null speed (schema permits it) counts as moving so we never drop real travel.
    if (i > 0 && (points[i].speed == null || points[i].speed >= SPEED_GATE_MPS)) {
      distance += haversineMeters(
        points[i - 1].lat, points[i - 1].lon,
        points[i].lat, points[i].lon
      );
    }
    if (points[i].speed != null) speeds.push(points[i].speed);
  }

  const maxSpeed = speeds.length ? Math.max(...speeds) : null;
  const avgSpeed = speeds.length ? speeds.reduce((a, b) => a + b, 0) / speeds.length : null;
  // GPS altitude is too noisy to sum into a trustworthy cumulative gain (it scales
  // with sample count, not terrain), so report the honest min-to-max range instead.
  // A real ascent figure will come from the BME680 barometer (see sensor platform plan).
  const elevRange = (elevMin != null && elevMax != null) ? elevMax - elevMin : null;
  const durationMs = new Date(points.at(-1).timestamp) - new Date(points[0].timestamp);

  return { distance, maxSpeed, avgSpeed, elevRange, durationMs, pointCount: points.length };
}

function fmtDistance(meters) {
  const miles = meters / 1609.344;
  return `${miles >= 10 ? miles.toFixed(1) : miles.toFixed(2)} mi`;
}

function fmtSpeed(mps) {
  return mps != null ? `${(mps * 2.23694).toFixed(1)} mph` : '—';
}

function fmtAltitude(meters) {
  return meters != null ? `${Math.round(meters * 3.28084)} ft` : '—';
}

function fmtDuration(ms) {
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function fmtTime(isoString) {
  return new Date(isoString).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function fmtDate(isoString) {
  return new Date(isoString).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
}
