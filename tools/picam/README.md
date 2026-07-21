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
sudo install -m 0755 cam-stream.sh /usr/local/bin/cam-stream.sh
sudo install -m 0644 cam-stream-cam1.env /etc/default/cam-stream-cam1   # edit per host
sudo install -m 0644 cam-stream@.service /etc/systemd/system/cam-stream@.service
sudo systemctl daemon-reload
sudo systemctl enable --now cam-stream@cam1                             # instance = MediaMTX path
```

- `cam-stream.sh` — ffmpeg wrapper; all config via the `EnvironmentFile`.
- `cam-stream@.service` — systemd template; instance name = the MediaMTX path (`cam1`…).
- `cam-stream-cam1.env` — picam1's config. `CAM_DEVICE` is pinned by USB `by-path` so
  it survives video-node renumbering; retarget it per Pi/port.

Known gap: if the V4L2/HW-encoder state wedges (e.g. after force-killing ffmpeg
mid-stream), ffmpeg blocks in D-state and `Restart=always` can't recover it — only a
reboot clears it. A frame-flow watchdog is the fix to add before the event.
