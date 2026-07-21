#!/usr/bin/env bash
# Frame-flow watchdog for cam-stream@<instance>.
#
# Detects a stalled encoder (ffmpeg output no longer advancing) and recovers.
# The output signal is ffmpeg's -progress out_time_us. Only a *stably running*
# instance is judged: if the service is down or its MainPID changed, it is
# already flapping/restarting on its own (Restart=always) -- e.g. an SRT/network
# drop -- so the watchdog rebaselines and stays out.
#
# Recovery is scoped to the failure it targets:
#   - stalled AND process in D (uninterruptible) state  -> device wedge a restart
#     cannot clear -> reboot (rate-limited so it can never boot-loop).
#   - stalled in any other state -> restart the service (no reboot). If the
#     underlying wedge persists, the fresh ffmpeg hangs in D and is rebooted on a
#     later cycle -- so escalation is automatic.
#
# Runs as root (needs systemctl restart/reboot). See tools/picam/README.md.
set -uo pipefail

INSTANCE="${1:?usage: cam-watchdog.sh <instance>}"      # e.g. cam1
SVC="cam-stream@${INSTANCE}.service"
PROGRESS="/run/cam-stream/${INSTANCE}.progress"
STATE_DIR="${STATE_DIRECTORY:-/var/lib/cam-watchdog}"
REBOOT_STAMP="${STATE_DIR}/${INSTANCE}.last_reboot"

CHECK_INTERVAL="${CHECK_INTERVAL:-10}"                  # seconds between checks
STALL_SECONDS="${STALL_SECONDS:-40}"                    # frozen output this long => stalled
MIN_REBOOT_INTERVAL="${MIN_REBOOT_INTERVAL:-600}"       # never reboot more often than this

log() { echo "cam-watchdog[$INSTANCE]: $*"; }
now() { date +%s; }

last_out() {   # latest out_time_us from the tail of the progress file, or empty
  [ -r "$PROGRESS" ] || return 0
  tail -c 4096 "$PROGRESS" 2>/dev/null | grep -a 'out_time_us=' | tail -1 | cut -d= -f2
}
proc_state() {  # single-char state (D/S/R/T/...) of a pid, or empty
  local pid="$1"
  { [ -n "$pid" ] && [ "$pid" != 0 ]; } || return 0
  awk '/^State:/{print $2; exit}' "/proc/$pid/status" 2>/dev/null
}
guard_ok() {   # true unless we rebooted (for this instance) too recently
  local last=0
  [ -r "$REBOOT_STAMP" ] && last="$(cat "$REBOOT_STAMP" 2>/dev/null || echo 0)"
  [ "$(( $(now) - last ))" -ge "$MIN_REBOOT_INTERVAL" ]
}

log "started: svc=$SVC progress=$PROGRESS check=${CHECK_INTERVAL}s stall=${STALL_SECONDS}s"
last_pid=""; last_out=""; frozen_since="$(now)"

while sleep "$CHECK_INTERVAL"; do
  active="$(systemctl is-active "$SVC" 2>/dev/null)"
  pid="$(systemctl show -p MainPID --value "$SVC" 2>/dev/null)"
  out="$(last_out)"

  # Judge only a stably-running instance; anything else => rebaseline and wait.
  if [ "$active" != "active" ] || [ -z "$pid" ] || [ "$pid" = 0 ] || [ "$pid" != "$last_pid" ]; then
    last_pid="$pid"; last_out="$out"; frozen_since="$(now)"
    continue
  fi

  # Same pid as last cycle: is output advancing?
  if [ -n "$out" ] && [ "$out" != "$last_out" ]; then
    last_out="$out"; frozen_since="$(now)"        # advancing => healthy
    continue
  fi

  # Output frozen (or never appeared) with a stable pid.
  [ "$(( $(now) - frozen_since ))" -ge "$STALL_SECONDS" ] || continue

  st="$(proc_state "$pid")"
  log "STALL: pid=$pid state=${st:-?} out=${out:-none} frozen>=${STALL_SECONDS}s"

  if [ "$st" = "D" ]; then
    if guard_ok; then
      log "device-wedged (D); rebooting to recover"
      echo "$(now)" > "$REBOOT_STAMP" 2>/dev/null || true
      systemctl reboot
      exit 0
    fi
    log "device-wedged (D) but within ${MIN_REBOOT_INTERVAL}s reboot guard; backing off"
    frozen_since="$(now)"
  else
    log "soft stall (state=${st:-?}); restarting $SVC"
    timeout 30 systemctl restart "$SVC" || log "restart failed/timed out"
    last_pid=""; last_out=""; frozen_since="$(now)"   # rebaseline after restart
  fi
done
