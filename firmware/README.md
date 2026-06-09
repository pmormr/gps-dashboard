# Firmware — ESP32 sensor nodes

ESPHome configs for the remote sensor nodes that publish onto the gps-dashboard
MQTT taxonomy (`sensors/<node>/<type>`). Each node is a small board near a sensor;
the Pi just ingests what they publish.

| Config | Board | Sensor | Topic |
|--------|-------|--------|-------|
| `cabin-bme680.yaml` | Seeed XIAO ESP32-C6 | Adafruit BME680 (BSEC2 IAQ) | `sensors/cabin/bme680` |

## Why ESPHome (not MicroPython)

A real air-quality number needs Bosch's BSEC2 library, which is a closed C blob —
no MicroPython binding exists. ESPHome's `bme68x_bsec2` component runs BSEC2 on the
ESP-IDF framework (required for the RISC-V C6) and handles Wi-Fi, MQTT, NTP, OTA,
and reconnect, so the node is a config file instead of a hand-rolled C daemon.

## Build / flash

ESPHome and the ESP-IDF toolchain are an **online prep step** (like building the
PMTiles archive). The node itself runs fully offline on the van LAN at runtime —
MQTT to the Pi broker, SNTP off the Pi.

```bash
cp firmware/secrets.yaml.example firmware/secrets.yaml   # then edit (gitignored)
uv tool run esphome run firmware/cabin-bme680.yaml        # first flash over USB; OTA after
```

## MQTT contract

The combined JSON published to `sensors/<node>/bme680` uses keys that are exactly
the `bme680_readings` column names (`ts`, `temp_c`, `humidity_pct`, `pressure_hpa`,
`gas_ohms`, `iaq`, `iaq_accuracy`, `co2_equivalent`, `breath_voc_equivalent`), so
`mqttbus/ingest.py` stores it with no per-node config. Birth/will messages on
`.../status` provide the `online`/`offline` LWT the ingest applies to the registry.
