# Victron house-power platform

Bridges the van's **Victron Venus OS GX** power system into the sensor platform as a
third stream (after `cabin/bme680` and `van/obd`), so battery / solar / inverter
state lands in the same SQLite DB and joins GPS on the canonical ms timestamp (e.g.
per-trip energy use). Mirrors the OBD effort: a new stream is a `READING_TABLES`
spec entry + a reader + a service unit, not a new pipeline.

## Device facts (discovered 2026-06-23)

- Venus OS GX at `192.168.42.234` (DHCP), **portal-id `c0619aba56e5`**.
- **MQTT on LAN** enabled, plain `:1883` + SSL `:8883`. **Both require the password
  `van123!!!`**; the username is ignored. We use plain `:1883`.
- 24 V system: SmartShunt battery monitor, MultiPlus inverter/charger (`vebus`), MPPT
  (`solarcharger`), Cerbo-class GX. Temp inputs unwired, digital inputs disabled,
  Fronius/Shelly idle. The `system/0/*` tree is the clean aggregated read surface.
- **Venus quirks:** topics are `N/<portal>/<service>/<instance>/<path>` as
  `{"value": …}`; the device **stops publishing ~60 s after connect unless it gets a
  keepalive** (empty publish to `R/<portal>/keepalive`); device **instance numbers
  are not stable** across reconfigurations (wildcard them; `system` is always 0).
  Writes go to `W/` — never touched.

## Architecture

`sensors/victron_reader.py` runs on the Pi with **two MQTT clients**:

- **source** → the GX broker (`VICTRON_MQTT_*`, authenticated): subscribes
  `N/+/{system,solarcharger,vebus}/#`, learns the portal-id from the first message,
  sends the keepalive, and keeps a latest-value cache (topic→column via an
  instance-wildcarded map).
- **sink** → the Pi mosquitto (anonymous, our usual LWT-on-`status`): every
  `PUBLISH_INTERVAL_S` (30 s, matching BME680) emits one `sensors/house/victron`
  snapshot, which `mqtt-ingest` writes to `victron_readings`.

Not engine-gated (the GX is always on); instead a **staleness watchdog** flips the
stream offline and stops emitting if the GX goes silent, rather than republish a
frozen cache (logger ethos). `--fake` swaps a synthetic source, real sink.

## Status

- [x] **Phase 0 — discover.** Topic tree dumped, schema grounded, auth/keepalive
      understood.
- [x] **Phase 1 — schema + reader + unit + tests** (this branch): `victron` entry in
      `api/sensor_schema.py`, `victron_readings` in `api/db.py`, `victron_reader.py`,
      `deploy/sensor-victron.service`, `tests/test_victron_reader.py` + an ingest case.
- [ ] **Phase 2 — deploy.** Push; on the Pi create `/etc/default/gps-victron`
      (root, 0600) with `VICTRON_MQTT_PASSWORD=…`, **add `sensor-victron` to the
      post-receive hook's sensor-restart list**, `systemctl enable --now`. Verify rows
      land and `/sensors` shows the `house/victron` card (registry-driven, free).
- [ ] **Phase 3 — correlation/extras (deferred).** Lifetime solar yield, AC-out,
      per-trip energy join; revisit temp inputs if probes get wired.

## Schema (`victron_readings`, all from `system/0` unless noted)

`battery_soc/voltage/current/power/temp_c` (`Dc/Battery/*`), `consumed_ah`,
`time_to_go_s`, `battery_state`; `pv_power` (`Dc/Pv/Power`), `pv_voltage` +
`pv_yield_today_kwh` + `solar_state` (`solarcharger/*`); `dc_system_power`
(`Dc/System/Power`); `ac_in_power/current/source` (`Ac/ActiveIn/*`),
`ac_consumption_power` (`Ac/Consumption/L1/Power`); `vebus_state` + `vebus_mode`
(`vebus/*`). Column set is the `READING_TABLES['victron']` spec, not hand-written SQL.

## Secret handling

The GX password is the only secret and stays **out of git**: the service unit reads
`EnvironmentFile=-/etc/default/gps-victron` (optional `-`), created per-Pi with
`VICTRON_MQTT_PASSWORD=…`. `VICTRON_MQTT_HOST` defaults to the GX IP in the unit.
