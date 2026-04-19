function haversineMeters(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const φ1 = lat1 * Math.PI / 180, φ2 = lat2 * Math.PI / 180;
  const Δφ = (lat2 - lat1) * Math.PI / 180;
  const Δλ = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(Δφ / 2) ** 2 + Math.cos(φ1) * Math.cos(φ2) * Math.sin(Δλ / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function computeStats(points) {
  if (!points.length) return null;

  let distance = 0;
  let elevGain = 0;
  const speeds = [];

  for (let i = 0; i < points.length; i++) {
    if (i > 0) {
      distance += haversineMeters(
        points[i - 1].lat, points[i - 1].lon,
        points[i].lat, points[i].lon
      );
      const prev = points[i - 1].altitude, curr = points[i].altitude;
      if (prev != null && curr != null && curr > prev) elevGain += curr - prev;
    }
    if (points[i].speed != null) speeds.push(points[i].speed);
  }

  const maxSpeed = speeds.length ? Math.max(...speeds) : null;
  const avgSpeed = speeds.length ? speeds.reduce((a, b) => a + b, 0) / speeds.length : null;
  const durationMs = new Date(points.at(-1).timestamp) - new Date(points[0].timestamp);

  return { distance, maxSpeed, avgSpeed, elevGain, durationMs, pointCount: points.length };
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
