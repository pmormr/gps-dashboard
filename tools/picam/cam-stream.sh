#!/usr/bin/env bash
# Hill-climb camera encoder: capture a USB webcam, hardware-encode H.264, and
# SRT-publish to the MediaMTX hub on pmpi1. Config comes from the systemd
# EnvironmentFile (/etc/default/cam-stream-<instance>). See paul-network-docs
# van/devices/picam1.md and gps-dashboard plans/streaming-platform-plan.md.
set -euo pipefail
: "${CAM_DEVICE:?CAM_DEVICE required}" "${CAM_PATH:?CAM_PATH required}" "${HUB_HOST:?HUB_HOST required}"
CAM_W="${CAM_W:-1280}"
CAM_H="${CAM_H:-720}"
CAM_FPS="${CAM_FPS:-10}"
CAM_BITRATE="${CAM_BITRATE:-2500k}"
HUB_PORT="${HUB_PORT:-8890}"
# Machine-readable progress for the frame-flow watchdog (cam-watchdog@%i). Under
# systemd RUNTIME_DIRECTORY=/run/cam-stream (tmpfs); /tmp when run by hand.
PROGRESS="${RUNTIME_DIRECTORY:-/tmp}/${CAM_PATH}.progress"
exec ffmpeg -hide_banner -loglevel warning -nostdin \
  -progress "file:${PROGRESS}" -stats_period 2 \
  -f v4l2 -input_format yuyv422 -video_size "${CAM_W}x${CAM_H}" -framerate "${CAM_FPS}" -i "${CAM_DEVICE}" \
  -c:v h264_v4l2m2m -b:v "${CAM_BITRATE}" -g "$((CAM_FPS * 2))" \
  -f mpegts "srt://${HUB_HOST}:${HUB_PORT}?streamid=publish:${CAM_PATH}&pkt_size=1316"
