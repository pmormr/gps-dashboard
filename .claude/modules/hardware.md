# Hardware, gpsd & NTP

The physical GPS + timing layer: the GPS module, gpsd, PPS, and chrony/NTP — plus the
`tools/` setup/validation scripts and `deploy/` config templates.

## GPS module (current)

A u-blox **NEO-M9N** (4-constellation: GPS + GLONASS + Galileo + BeiDou, plus
SBAS/QZSS; firmware SPG 4.04, PROTVER 32.01) wired to the Raspberry Pi (CM5) GPIO
header. gpsd reads it as **UBX binary** (not NMEA) on the primary header UART
`/dev/ttyAMA0` at 38400 baud — gpsd auto-configures the M9N on attach (NMEA off, UBX
NAV-PVT/SAT/DOP on), so TPV carries per-fix accuracy (`epx`/`epy`) and `SKY` is fully
populated. The module's TIMEPULSE is wired to GPIO 4 and read via the `pps-gpio`
overlay → `/dev/pps0`. gpsd and the logger both reference `/dev/ttyAMA0`. NTP runs in
GPS+PPS mode (chrony stratum 1, sub-microsecond accuracy via PPS).

(gpsd also exposes a phantom `/dev/pps1` from attaching the PPS line discipline to the
UART; nothing is wired to it. Chrony only uses `/dev/pps0`.)

## Baud trap (why 38400)

The module runs at 38400 — its factory default — set via `GPSD_OPTIONS="-n -s 38400"`
in `/etc/default/gpsd`. **The rule is "match the module's reset-default baud rate," not
the literal number.** An earlier attempt to drive the previous module at 115200 was lost
when its config-backup power drained (cable borrowed mid-trip), reverting it to its
factory default on the next reboot while gpsd kept forcing 115200 — gpsd then silently
received nothing and the logger stalled invisibly for days. Keeping gpsd pointed at the
module's reset default means a power loss can't desync them. PPS, not baud, drives
timing precision, so the headline number is irrelevant — 38400 comfortably carries the
**5 Hz UBX** nav stream from all four constellations. The nav rate (`CFG-RATE-MEAS`) is
set to 200 ms / 5 Hz and persisted to flash; gpsd never *forces* a rate (unlike baud),
so a flash revert to the 1 Hz factory default is graceful, not a silent stall.

## Legacy hardware (eliminated)

- The immediately previous module was a serial GPS at 9600 baud (its factory default);
  the M9N replaces it on the same UART and same GPIO 4 PPS pin, just at a higher baud rate.
- Before that, a u-blox 7 USB dongle (VID 1546, PID 01a7) pinned to `/dev/gps0` via
  `deploy/99-gps-dongle.rules` and run GPS-only (stratum 10, ~100ms). That udev rule and
  the `/dev/gps0` path apply only to the USB dongle, not the current serial GPS.

## Setup & validation (CLI, not the web UI)

Setup/validation are CLI scripts in `tools/`; service config templates live in `deploy/`.

- `tools/gpsd_setup.py` — interactive: detects devices, writes `/etc/default/gpsd`,
  restarts gpsd. For USB serial devices (ttyACM*/ttyUSB*), reads VID/PID via udevadm and
  offers to install the udev rule and switch to `/dev/gps0`. After restart, polls until
  gpsd is active and a TPV fix (mode ≥ 2) is received (up to 90s) before validating.
- `deploy/99-gps-dongle.rules` — **legacy USB dongle only** (not the current serial GPS).
  udev rule pinning the u-blox dongle (VID 1546, PID 01a7) to `/dev/gps0` and notifying
  gpsd via `gpsdctl add` on every plug-in, so gpsd re-attaches whenever the dongle
  re-enumerates. `gpsd_setup.py` installs it for USB devices. Manual install:
  `sudo cp deploy/99-gps-dongle.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger`.
- `tools/gpsd_validate.py` — checks service, device, fix, data flow; prints PASS/FAIL.
- `tools/ntp_setup.py` — interactive: configures chrony with GPS SHM source, optional
  PPS; enables the Pi as a LAN NTP server.
- `tools/ntp_validate.py` — checks chrony sync, GPS/PPS source, stratum, LAN serving.

Two chrony config templates:
- `deploy/chrony-gps-pps.conf` — **current**: serial GPS with PPS (sub-microsecond, stratum 1).
- `deploy/chrony-gps-only.conf` — legacy USB dongle, no PPS (~100ms, stratum 10).
