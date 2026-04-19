document.addEventListener('DOMContentLoaded', () => {
  MapView.init('map');
  TripsMap.init('trips-map');

  Timeline.init();
  Trips.init();

  // Tab switching
  document.querySelectorAll('#tab-bar button').forEach(btn => {
    btn.addEventListener('click', () => {
      const view = btn.dataset.view;
      document.querySelectorAll('#tab-bar button').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(`view-${view}`).classList.add('active');
      MapView.invalidateSize();
      TripsMap.invalidateSize();
    });
  });
});
