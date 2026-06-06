import json
import socket
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from api.db import get_connection, init_db, migrate

LOG_INTERVAL_SECONDS = 5
SOCKET_TIMEOUT_SECONDS = 30
HEARTBEAT_SECONDS = 60
STALE_RECONNECT_SECONDS = 120
GPS_TIME_MAX_AGE_SECONDS = 10


@dataclass
class LoggerStats:
    """Rolling counters for the current heartbeat window.

    Tracks how many TPV records were written versus dropped, broken down by
    reason, so a stalled stream reveals its cause in the journal instead of
    failing silently. Per-window counters reset on each heartbeat; last-seen
    state (fix mode, heartbeat clock) carries across windows and reconnects.
    """

    written: int = 0
    no_fix: int = 0
    no_latlon: int = 0
    bad_range: int = 0
    null_island: int = 0
    stale_time: int = 0
    throttled: int = 0
    json_err: int = 0
    last_fix_mode: int = 0
    last_heartbeat: float = field(default_factory=time.monotonic)

    def reset_window(self) -> None:
        """Zero the per-window counters, preserving last-seen state."""
        self.written = self.no_fix = self.no_latlon = 0
        self.bad_range = self.null_island = self.stale_time = 0
        self.throttled = self.json_err = 0

    def heartbeat_line(self, write_age: float | None) -> str:
        """Build a one-line summary of the current window.

        Args:
            write_age: Seconds since the last point was written, or None if
                nothing has been written yet this run.

        Returns:
            A human-readable heartbeat string for the journal.
        """
        age = f"{write_age:.0f}s" if write_age is not None else "never"
        return (
            f"heartbeat: wrote={self.written} mode={self.last_fix_mode} "
            f"last_write={age} | dropped no_fix={self.no_fix} "
            f"no_latlon={self.no_latlon} bad_range={self.bad_range} "
            f"null_island={self.null_island} stale_time={self.stale_time} "
            f"throttled={self.throttled} json_err={self.json_err}"
        )


def run_session(conn: sqlite3.Connection, last_log_time: float,
                stats: LoggerStats) -> float:
    """Stream TPV records from gpsd into the database for one connection.

    Connects to gpsd, watches the JSON feed, and inserts valid fixes throttled
    to one per LOG_INTERVAL_SECONDS. Emits a heartbeat every HEARTBEAT_SECONDS
    and returns early (forcing a reconnect) if no valid fix is seen for
    STALE_RECONNECT_SECONDS while data is still arriving — the case the raw
    socket timeout cannot detect, because gpsd keeps emitting SKY/no-fix TPV.

    Args:
        conn: Open SQLite connection used for inserts.
        last_log_time: Monotonic time of the last successful write, carried
            across sessions to preserve the write throttle.
        stats: Rolling counters, mutated in place across sessions.

    Returns:
        The updated last-write monotonic time.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(SOCKET_TIMEOUT_SECONDS)
    try:
        sock.connect(('127.0.0.1', 2947))
        sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
        f = sock.makefile('r', encoding='utf-8', errors='replace')
        last_fix_time = time.monotonic()
        for line in f:
            now = time.monotonic()

            if now - stats.last_heartbeat >= HEARTBEAT_SECONDS:
                write_age = now - last_log_time if last_log_time > 0 else None
                print(stats.heartbeat_line(write_age), flush=True)
                stats.reset_window()
                stats.last_heartbeat = now

            if now - last_fix_time >= STALE_RECONNECT_SECONDS:
                print(
                    f"No valid fix in {STALE_RECONNECT_SECONDS}s "
                    f"(mode={stats.last_fix_mode}) despite data flow; "
                    "reconnecting",
                    file=sys.stderr, flush=True,
                )
                return last_log_time

            try:
                report = json.loads(line)
            except json.JSONDecodeError:
                stats.json_err += 1
                continue

            if report.get('class') != 'TPV':
                continue

            mode = report.get('mode', 0)
            stats.last_fix_mode = mode
            if mode < 2:
                stats.no_fix += 1
                continue
            lat = report.get('lat')
            lon = report.get('lon')
            if lat is None or lon is None:
                stats.no_latlon += 1
                continue
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                stats.bad_range += 1
                continue
            # Null Island: gpsd briefly reports mode>=2 with uninitialized 0,0
            # coords during driver init or transient fix loss.
            if lat == 0 and lon == 0:
                stats.null_island += 1
                continue

            # Reject fixes whose GPS time is more than 10s behind wall clock.
            # Guards against gpsd replaying stale cached positions after restart.
            gps_time_str = report.get('time')
            if gps_time_str:
                try:
                    gps_dt = datetime.fromisoformat(gps_time_str.replace('Z', '+00:00'))
                    age = (datetime.now(timezone.utc) - gps_dt).total_seconds()
                    if age > GPS_TIME_MAX_AGE_SECONDS:
                        stats.stale_time += 1
                        continue
                except ValueError:
                    pass

            # A usable fix arrived; the stream is alive even if we throttle it.
            last_fix_time = now
            if now - last_log_time < LOG_INTERVAL_SECONDS:
                stats.throttled += 1
                continue

            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            conn.execute(
                "INSERT INTO gps_points (timestamp, lat, lon, speed, altitude, track) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    timestamp,
                    lat,
                    lon,
                    report.get('speed'),
                    report.get('alt'),
                    report.get('track'),
                ),
            )
            conn.commit()
            last_log_time = now
            stats.written += 1
    finally:
        sock.close()

    return last_log_time


def main() -> None:
    """Run the logger loop, reconnecting to gpsd on any failure."""
    conn = get_connection()
    init_db(conn)
    migrate(conn)

    print("GPS logger started", flush=True)
    last_log_time = 0.0
    stats = LoggerStats()

    while True:
        try:
            last_log_time = run_session(conn, last_log_time, stats)
        except KeyboardInterrupt:
            print("GPS logger stopped", flush=True)
            break
        except Exception as e:
            print(f"GPS error: {e}, reconnecting in 5s", file=sys.stderr, flush=True)
            time.sleep(5)


if __name__ == '__main__':
    main()
