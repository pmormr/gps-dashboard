# OBD-II Supported-PID Reference — 2021 Ram ProMaster 2500

> Phase 0 capability dump for the van. Captured by `tools/obd_probe.py` on the Pi
> through the **12+8 SGW-bypass harness** + OBDLink EX, **2026-06-22**. This is the
> authoritative list of what this vehicle's ECUs actually report — every later
> decision about which PIDs to log is made against this, not against assumptions.
> When the OBD platform lands, the durable bits fold into `.claude/modules/sensors.md`
> and this file can go.

## Connection facts

| Field | Value |
|-------|-------|
| Vehicle | 2021 Ram ProMaster 2500, 3.6 L Pentastar V6, **gasoline** |
| Adapter | OBDLink EX (genuine ScanTool STN2232 v5.12.4), USB `/dev/ttyUSB0` |
| Access | via 12+8 Security-Gateway **bypass harness** (the SGW blocks the plain OBD port) |
| Protocol | **ISO 15765-4 (CAN, 29-bit headers, 500 kbaud)** — python-OBD protocol id **7** |
| Status | `CAR_CONNECTED` |
| Supported commands | **126** |
| Stored DTCs | none (MIL off, DTC count 0) |
| Throughput | ~22 queries/s (fast mode off) → ~0.49 s for an 11-PID snapshot |
| Voltage signature | parked/KOEO ~12.0–12.3 V · engine running ~13.8–14.1 V (alternator) |

**Protocol note:** the bus is **29-bit** CAN, not the 11-bit the plan originally
assumed. python-OBD auto-detects it; no forced `ATSP` needed.

## Notable absences (shape the data model)

| PID | Name | Why it matters |
|-----|------|----------------|
| `0110` | MAF (mass airflow) | **Absent** — confirms the Pentastar is **speed-density** (MAP + IAT, no MAF sensor), as predicted. |
| `015E` | Fuel rate | **Absent** — no native fuel-flow reading. Fuel rate must be **derived** from the speed-density inputs below (open decision B). |

---

## Mode 01 — live data (the loggable PIDs)

The signals that change in real time. **→ col** marks PIDs slated for an
`obd_readings` column (see the schema discussion in `obd-platform-plan.md`).

| PID | Name | Description | Logged |
|-----|------|-------------|--------|
| `0103` | FUEL_STATUS | Fuel system status (open/closed loop) | |
| `0104` | ENGINE_LOAD | Calculated engine load | **→ col** |
| `0105` | COOLANT_TEMP | Engine coolant temperature | **→ col** |
| `0106` | SHORT_FUEL_TRIM_1 | Short-term fuel trim, bank 1 | **→ col** (fuel-rate input) |
| `0107` | LONG_FUEL_TRIM_1 | Long-term fuel trim, bank 1 | **→ col** (fuel-rate input) |
| `0108` | SHORT_FUEL_TRIM_2 | Short-term fuel trim, bank 2 | **→ col** (fuel-rate input) |
| `0109` | LONG_FUEL_TRIM_2 | Long-term fuel trim, bank 2 | **→ col** (fuel-rate input) |
| `010B` | INTAKE_PRESSURE | Intake manifold abs. pressure (MAP) | **→ col** (speed-density) |
| `010C` | RPM | Engine RPM | **→ col** |
| `010D` | SPEED | Vehicle speed | **→ col** (vs GPS speed) |
| `010E` | TIMING_ADVANCE | Ignition timing advance | |
| `010F` | INTAKE_TEMP | Intake air temperature (IAT) | **→ col** (speed-density) |
| `0111` | THROTTLE_POS | Throttle position | **→ col** |
| `0113` | O2_SENSORS | O2 sensors present (bitmap) | |
| `0114` | O2_B1S1 | O2 bank 1 sensor 1 voltage | |
| `0115` | O2_B1S2 | O2 bank 1 sensor 2 voltage | |
| `0118` | O2_B2S1 | O2 bank 2 sensor 1 voltage | |
| `0119` | O2_B2S2 | O2 bank 2 sensor 2 voltage | |
| `011C` | OBD_COMPLIANCE | OBD standards compliance | |
| `011F` | RUN_TIME | Engine run time since start | **→ col** |
| `0121` | DISTANCE_W_MIL | Distance traveled with MIL on | |
| `012E` | EVAPORATIVE_PURGE | Commanded evaporative purge | |
| `012F` | FUEL_LEVEL | Fuel level input (coarse) | **→ col** |
| `0130` | WARMUPS_SINCE_DTC_CLEAR | Warm-ups since codes cleared | |
| `0131` | DISTANCE_SINCE_DTC_CLEAR | Distance since codes cleared | |
| `0132` | EVAP_VAPOR_PRESSURE | EVAP system vapor pressure | |
| `0133` | BAROMETRIC_PRESSURE | Barometric pressure | **→ col** (speed-density) |
| `013C` | CATALYST_TEMP_B1S1 | Catalyst temp, bank 1 sensor 1 | |
| `013D` | CATALYST_TEMP_B2S1 | Catalyst temp, bank 2 sensor 1 | |
| `0141` | STATUS_DRIVE_CYCLE | Monitor status this drive cycle | |
| `0142` | CONTROL_MODULE_VOLTAGE | Control-module (chassis) voltage | **→ col** |
| `0143` | ABSOLUTE_LOAD | Absolute load value | **→ col** (fuel-rate input) |
| `0144` | COMMANDED_EQUIV_RATIO | Commanded equivalence ratio (λ) | **→ col** (fuel-rate input) |
| `0145` | RELATIVE_THROTTLE_POS | Relative throttle position | |
| `0146` | AMBIANT_AIR_TEMP | Ambient air temperature | **→ col** (correlation) |
| `0147` | THROTTLE_POS_B | Absolute throttle position B | |
| `0149` | ACCELERATOR_POS_D | Accelerator pedal position D | |
| `014A` | ACCELERATOR_POS_E | Accelerator pedal position E | |
| `014C` | THROTTLE_ACTUATOR | Commanded throttle actuator | |
| `0151` | FUEL_TYPE | Fuel type | |

Plus the support bitmaps `0100`/`0120`/`0140` (PIDS_A/B/C).

## Mode 02 — freeze-frame

Mode 02 mirrors **every** Mode 01 PID above with a `DTC_` prefix (`0203`–`0251`):
the freeze-frame snapshot captured when a DTC sets. Same signals, not separate
capabilities — not logged continuously; useful only when diagnosing a stored code.

## Mode 06 — on-board monitor results (MIDs)

Component self-test results. Not a continuous-logging target; valuable for a future
health/diagnostics surface.

| MID | Monitor |
|-----|---------|
| `0601`–`0606` | O2 sensor monitors (B1S1, B1S2, B2S1, B2S2) |
| `0621`–`0622` | Catalyst monitors (bank 1, bank 2) |
| `0635`–`0636` | VVT monitors (bank 1, bank 2) |
| `0639`–`063D` | EVAP monitors (0.150"/0.090"/0.020") + purge flow |
| `0641`–`0646` | O2 sensor heater monitors |
| `0681`–`0682` | Fuel system monitors (bank 1, bank 2) |
| `06A2`–`06A7` | **Misfire monitors, cylinders 1–6** |

## Modes 03 / 04 / 07 / 09 — diagnostics & info

| Cmd | Function |
|-----|----------|
| `03` | Get stored DTCs |
| `04` | Clear DTCs + freeze-frame data |
| `07` | Get DTCs from the current/last drive cycle |
| `0902` | VIN |
| `0904` | Calibration ID |
| `0906` | Calibration verification numbers (CVN) |

## Adapter (ELM/AT) commands

| Cmd | Function |
|-----|----------|
| `ATI` | ELM327 version string |
| `ATRV` | Adapter-measured voltage (answers with ignition off — liveness + chassis voltage) |

---

## Sample readings (engine idling, KOEO→warm), 2026-06-22

From `/mnt/nvme/data/obd_phase0_idle.jsonl` on the Pi (90 rows / 60 s):

- RPM 1173 (cold fast-idle) → ~764 (warm idle)
- MAP 98 kPa (engine off, atmospheric) → ~49–53 kPa (idle manifold vacuum)
- Coolant 32 → 52 °C (warming); intake ~33 °C
- Engine load ~44–48 %; throttle at closed baseline; speed 0
- Control-module voltage ~14.0–14.1 V; ELM voltage ~13.8–13.9 V (alternator up)
- Fuel level steady 64.7 %
