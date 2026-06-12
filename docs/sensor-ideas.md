# Sensor Ideas (backlog)

Candidate sensors to add to the van platform, beyond the current GPS + BME680
air-quality streams. Filter applied: **data that changes meaningfully over a
long drive and is interesting to paint onto a GPS track.** Prices are rough
(2026) and for the bare module / common breakout. Integration assumes the
existing path: ESPHome on an ESP32 node → combined JSON over MQTT →
`mqttbus/ingest.py` → SQLite → `/sensors` chart.

Status: brainstorm only — nothing ordered or scoped yet.

## Tier 1 — strongest road-trip payoff

### ☢️ Radiation / Geiger counter — *first pick*
Background radiation varies with the two things you drive through:
- **Altitude**: cosmic-ray flux roughly doubles every ~1,500–2,000 m. Mountain
  passes (Trail Ridge, Sierra, Colorado) light up in real time and fall on the
  descent — a clean, legible signal.
- **Geology**: granite, the uranium-rich Colorado Plateau, monazite sands →
  real terrestrial hot/cold zones over a long route.

GM tube modules emit a TTL pulse per event → ESPHome `pulse_counter` →
CPM → µSv/h. The lowest-friction electrical fit in this whole list.
- **CAJOE RadiationD v1.1** (J305 tube) — ~$30–40, cheap hacker standard, well
  documented for ESPHome.
- **GGreg20_V3** — ~$60–70, better engineered (isolated HV, clean pulse out);
  has an **official ESPHome external component** (`ggreg20_v3`, Oct 2025).

### 🫁 Real CO₂ — Sensirion SCD41 (~$25–35; ~$40 Grove/DFRobot breakout)
Upgrades the BME680's *fake* `co2_equivalent` (BSEC estimates it from VOCs) to a
true NDIR measurement (400–5,000 ppm). Practical value: sleeping in a sealed
cabin and windows-up driving drowsiness both show real CO₂ buildup. I²C, official
ESPHome `scd4x` component — drops onto an existing node.

### 💨 Particulate matter / PM2.5 — SDS011 (~$20–30) or Sensirion SPS30 (~$45–60)
The Western road-trip air story: wildfire smoke, dust, tunnels, city haze. Vivid
over a multi-state route. SDS011 = economical solid choice; SPS30 =
state-of-the-art (adds PM1.0/PM4.0, self-cleaning, ~10-yr life). UART, not I²C.

## Tier 2 — cheap, easy, worth tossing in

- **Outside air temp — DS18B20 waterproof** (~$5, 1-Wire). True outside temp vs
  the cabin BME680 → inside/outside delta on every trip. Trivial add.
- **UV index — LTR390** (~$8, I²C). Strong altitude + latitude + desert variation.
- **⚡ Lightning — AS3935 Franklin detector** (~$25–35). Estimates distance to the
  storm front (1–40 km, 14 steps). Use **SPI** wiring — the I²C path is flaky on
  this part per the ESPHome docs.

## Tier 3 — rich, but more work

- **OBD-II engine data** (ESP32 + ~$5 CAN transceiver, or an ELM327 dongle). MPG,
  coolant temp on long grades, intake-air temp. Deep dataset, but needs CAN
  wiring and per-vehicle PID fiddling.
- **IMU / road-roughness** (MPU6050 ~$4). Derive a road-quality / pothole map and
  cornering g-forces from accelerometer variance. Cheap HW, high data rate, real
  processing to make it meaningful.
- **Road-surface IR temp — MLX90614** (~$12). Non-contact surface temp → black-ice
  warning in winter.

## Software note (applies to any non-BME680 stream)
`mqttbus/ingest.py` currently writes specifically to `bme680_readings`. Each new
measurement type needs either its own table or a generalized readings schema,
plus an ingest topic handler, a registry row, and a `/sensors` chart. Per-sensor
software cost is mechanical but real — worth making the schema-generalization
decision deliberately the **first** time a non-BME680 stream lands. The Geiger
node is the natural forcing function for that.

## Recommended starting point
Geiger node first (explicit ask, best map dataset, clean `pulse_counter` job),
paired with the SCD41 (real CO₂ has safety value, rides along on I²C for almost
nothing), plus the $5 DS18B20 outside-temp probe for inside/outside delta.
