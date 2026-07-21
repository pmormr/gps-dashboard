# picam — hill-climb camera edge encoder

Standalone encoder for a Raspberry Pi + USB webcam at a hill-climb course
position: capture → hardware H.264 → SRT-publish to the MediaMTX hub on `pmpi1`.
One 720p camera per Pi (two 720p YUYV cams don't share a Pi's USB 2.0 bus). See
`plans/streaming-platform-plan.md` and, for the network side, `paul-network-docs`
`van/devices/picam1.md`.

These run on the **camera Pis, not `pmpi1`** — they are deliberately *outside* the
gps-dashboard deploy hook (which installs `deploy/*.service` onto `pmpi1`). Install
by hand:

```bash
sudo install -m 0755 cam-stream.sh cam-watchdog.sh /usr/local/bin/
sudo install -m 0644 cam-stream-cam1.env /etc/default/cam-stream-cam1      # edit per host
sudo install -m 0644 cam-stream@.service cam-watchdog@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cam-stream@cam1 cam-watchdog@cam1              # instance = MediaMTX path
```

## Encoder

- `cam-stream.sh` — ffmpeg wrapper; all config via the `EnvironmentFile`. Writes
  `-progress` to `/run/cam-stream/<path>.progress` (tmpfs) for the watchdog.
- `cam-stream@.service` — systemd template; instance name = the MediaMTX path (`cam1`…).
- `cam-stream-cam1.env` — picam1's config. `CAM_DEVICE` is pinned by USB `by-path` so
  it survives video-node renumbering; retarget it per Pi/port.

## Watchdog

`cam-watchdog@<path>` self-recovers a stalled encoder — the failure mode where a
wedged V4L2/HW-encoder ioctl leaves ffmpeg in **D (uninterruptible)** state, so it
produces no output *and* `Restart=always` can't kill it.

- Tracks ffmpeg's `out_time_us`. Advancing = healthy; frozen with a **stable PID** =
  stalled. A changing PID means the service is already flapping (e.g. an SRT/network
  drop, handled by `Restart=always`) — the watchdog rebaselines and stays out, so a
  network outage never triggers it.
- On a stall: **D-state → reboot** (the only cure for the wedge), **any other state →
  restart** the service. If a wedge survives the restart, the fresh ffmpeg hangs in D
  and is rebooted next cycle — escalation is automatic.
- Reboots are rate-limited (`MIN_REBOOT_INTERVAL`, default 600 s, persisted under
  `/var/lib/cam-watchdog/`) so it can never boot-loop. Runs as root.
- Tunables via the unit's `Environment=`: `CHECK_INTERVAL` (10 s), `STALL_SECONDS`
  (40 s), `MIN_REBOOT_INTERVAL` (600 s).
