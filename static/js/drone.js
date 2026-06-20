// Drone overlay controller. Toggling the 🚁 panel on lazily fetches *all* flights
// once (the dataset is tiny — ~10² flights — and MapLibre only draws what's in
// view) and hands them to MapView as a standalone layer, independent of the van
// time picker. Re-toggling reuses the cache; the fetch is deferred until first use.
const Drone = (() => {
  let cache = [];
  let loaded = false;
  let enabled = false;

  function setStatus(text) {
    const el = document.getElementById('drone-status');
    if (el) el.textContent = text;
  }

  async function load() {
    setStatus('Loading…');
    try {
      const data = await API.getDroneFlights();
      cache = data.flights || [];
      loaded = true;
      setStatus(`${cache.length} flight${cache.length === 1 ? '' : 's'}`);
      if (enabled) MapView.showDroneTracks(cache);
    } catch (e) {
      setStatus(`Error: ${e.message}`);
    }
  }

  async function setEnabled(on) {
    enabled = on;
    if (!on) {
      MapView.clearDroneTracks();
      return;
    }
    if (loaded) MapView.showDroneTracks(cache);
    else await load();
  }

  function init() {
    const toggle = document.getElementById('drone-toggle');
    if (!toggle) return;
    toggle.addEventListener('change', () => setEnabled(toggle.checked));
  }

  return { init };
})();
