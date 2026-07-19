import json
import socket
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from api.db import get_connection, init_db, now_canonical
from common.gpsd import GPSD_HOST, GPSD_PORT, WATCH
from common.timefmt import age_seconds

# Motion-gated raw write cadence: full nav rate while moving, throttled to
# ~1 Hz while parked. Parked 5 Hz is correlated bloat the processor's static hold
# collapses anyway; the moving rate is where the spatial fidelity lives. This gates
# only writes, layered on the live fix stream, so the freeze watchdog (which tracks
# the stream, not writes) is unaffected.
MOVING_WRITE_INTERVAL_SECONDS = 0.2  # 5 Hz
PARKED_WRITE_INTERVAL_SECONDS = 1.0  # ~1 Hz
PARKED_SPEED_MPS = 0.5  # below this Doppler speed, treat as parked

# SKY-sourced receiver telemetry throttle: DOP + sat counts to
# receiver_metadata, off the position hot-path and at its own cadence.
RECEIVER_METADATA_INTERVAL_SECONDS = 5

# Per-satellite SKY observation throttle (GNSS Observatory): the receiver's
# computed az/el/SNR for every positioned SV into sat_observations, at a far
# coarser cadence than receiver_metadata (orbits move only degrees/minute, so
# 60s is dense for both the obstruction map and orbit fitting). Same SKY message,
# independent throttle gate. See .claude/modules/observatory.md.
SAT_OBSERVATION_INTERVAL_SECONDS = 60

SOCKET_TIMEOUT_SECONDS = 30
HEARTBEAT_SECONDS = 60
STALE_RECONNECT_SECONDS = 120
GPS_TIME_MAX_AGE_SECONDS = 10

# Frozen-fix detection: a receiver can keep emitting valid fixes (mode>=2, sane
# lat/lon, fresh time) while its position never moves — a stuck nav solution that
# every other check reads as healthy. A live fix always jitters in the low digits
# even when parked, so a byte-identical position held this long means a freeze.
FROZEN_POSITION_SECONDS = 120
# Treat the fix stream as live only if a usable fix arrived this recently;
# otherwise "frozen" is unknowable (it's a no-fix gap, not a freeze).
FIX_FRESH_SECONDS = 15
# A fix-stream gap longer than this resets the freeze baseline, so reacquiring
# the same parked spot after losing sky view does not read as a freeze.
FIX_GAP_RESET_SECONDS = 30


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
    sky_written: int = 0
    sat_obs_written: int = 0
    last_fix_mode: int = 0
    last_heartbeat: float = field(default_factory=time.monotonic)
    last_position: tuple[float, float] | None = None
    position_since: float = 0.0
    last_usable_fix: float = 0.0
    last_sky_write: float = 0.0
    last_sat_obs_write: float = 0.0

    def reset_window(self) -> None:
        """Zero the per-window counters, preserving last-seen state."""
        self.written = self.no_fix = self.no_latlon = 0
        self.bad_range = self.null_island = self.stale_time = 0
        self.throttled = self.json_err = self.sky_written = 0
        self.sat_obs_written = 0

    def note_fix(self, lat: float, lon: float, now: float) -> None:
        """Record a usable fix for freeze tracking.

        Resets the freeze baseline when the position changes or when the fix
        stream had a gap (so reacquiring a parked spot is not mistaken for a
        freeze); otherwise the baseline persists so a held position ages.

        Args:
            lat: Fix latitude.
            lon: Fix longitude.
            now: Monotonic time of this fix.
        """
        pos = (lat, lon)
        gap = now - self.last_usable_fix if self.last_usable_fix else 0.0
        if self.last_position is None or pos != self.last_position or gap > FIX_GAP_RESET_SECONDS:
            self.last_position = pos
            self.position_since = now
        self.last_usable_fix = now

    def position_age(self, now: float) -> float | None:
        """Seconds the current position has been held, or None if unknowable.

        Returns None when no fix has been seen or the stream is not currently
        live (the no-fix case, which is not a freeze).

        Args:
            now: Current monotonic time.

        Returns:
            Age in seconds of the held position, or None.
        """
        if self.last_position is None or self.last_usable_fix == 0.0:
            return None
        if now - self.last_usable_fix >= FIX_FRESH_SECONDS:
            return None
        return now - self.position_since

    def is_frozen(self, now: float) -> bool:
        """Whether a live fix stream has held one position past the threshold.

        Args:
            now: Current monotonic time.

        Returns:
            True if the position is frozen, False otherwise.
        """
        age = self.position_age(now)
        return age is not None and age >= FROZEN_POSITION_SECONDS

    def heartbeat_line(self, now: float, write_age: float | None) -> str:
        """Build a one-line summary of the current window.

        Args:
            now: Current monotonic time, used to age the held position.
            write_age: Seconds since the last point was written, or None if
                nothing has been written yet this run.

        Returns:
            A human-readable heartbeat string for the journal.
        """
        age = f'{write_age:.0f}s' if write_age is not None else 'never'
        pos_age = self.position_age(now)
        pos = f'{pos_age:.0f}s' if pos_age is not None else 'n/a'
        frozen = 'yes' if self.is_frozen(now) else 'no'
        return (
            f'heartbeat: wrote={self.written} sky={self.sky_written} '
            f'satobs={self.sat_obs_written} mode={self.last_fix_mode} '
            f'last_write={age} pos_age={pos} frozen={frozen} | dropped '
            f'no_fix={self.no_fix} no_latlon={self.no_latlon} '
            f'bad_range={self.bad_range} null_island={self.null_island} '
            f'stale_time={self.stale_time} throttled={self.throttled} '
            f'json_err={self.json_err}'
        )


def sky_observation_rows(sats: list[dict[str, Any]], ts: str) -> list[tuple[Any, ...]]:
    """Build ``sat_observations`` rows from a gpsd SKY satellite array.

    Keeps only positioned satellites — both ``az`` and ``el`` present — since an
    unpositioned sat carries no angular information (same filter the live skyplot
    applies). Every row shares ``ts`` so one SKY sample forms one identifiable
    sweep. ``used`` is coerced to 0/1; missing optional fields stay None.

    Args:
        sats: The ``satellites`` array from a gpsd SKY report.
        ts: Canonical ms-UTC timestamp stamped on the whole sweep.

    Returns:
        One tuple per positioned sat, column order matching the
        ``sat_observations`` INSERT (timestamp, gnssid, svid, az, el, snr, used,
        health).
    """
    return [
        (
            ts,
            s.get('gnssid'),
            s.get('svid'),
            s.get('az'),
            s.get('el'),
            s.get('ss'),
            1 if s.get('used') else 0,
            s.get('health'),
        )
        for s in sats
        if s.get('az') is not None and s.get('el') is not None
    ]


def run_session(conn: sqlite3.Connection, last_log_time: float, stats: LoggerStats) -> float:
    """Stream TPV records from gpsd into the database for one connection.

    Connects to gpsd, watches the JSON feed, and inserts valid fixes at a
    motion-gated cadence (the full nav rate while moving, throttled to ~1 Hz
    while parked), with per-fix accuracy fields. SKY-sourced receiver telemetry
    (DOP + sat counts) is written to receiver_metadata on its own ~5 s throttle.
    Emits a heartbeat every HEARTBEAT_SECONDS
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
        sock.connect((GPSD_HOST, GPSD_PORT))
        sock.sendall(WATCH)
        f = sock.makefile('r', encoding='utf-8', errors='replace')
        last_fix_time = time.monotonic()
        for line in f:
            now = time.monotonic()

            if now - stats.last_heartbeat >= HEARTBEAT_SECONDS:
                write_age = now - last_log_time if last_log_time > 0 else None
                print(stats.heartbeat_line(now, write_age), flush=True)
                if stats.is_frozen(now):
                    print(
                        f'WARNING: position frozen at {stats.last_position} for '
                        f'{now - stats.position_since:.0f}s while fixes keep '
                        'arriving; receiver may need a cold start',
                        file=sys.stderr,
                        flush=True,
                    )
                stats.reset_window()
                stats.last_heartbeat = now

            if now - last_fix_time >= STALE_RECONNECT_SECONDS:
                print(
                    f'No valid fix in {STALE_RECONNECT_SECONDS}s '
                    f'(mode={stats.last_fix_mode}) despite data flow; '
                    'reconnecting',
                    file=sys.stderr,
                    flush=True,
                )
                return last_log_time

            try:
                report = json.loads(line)
            except json.JSONDecodeError:
                stats.json_err += 1
                continue

            if report.get('class') == 'SKY':
                sats = report.get('satellites') or []
                # Receiver telemetry on its own throttle, off the
                # position path. nSat/uSat when gpsd supplies them, else counted
                # from the satellite array.
                if now - stats.last_sky_write >= RECEIVER_METADATA_INTERVAL_SECONDS:
                    conn.execute(
                        'INSERT INTO receiver_metadata '
                        '(timestamp, hdop, vdop, pdop, nsat_used, nsat_seen) '
                        'VALUES (?, ?, ?, ?, ?, ?)',
                        (
                            now_canonical(),
                            report.get('hdop'),
                            report.get('vdop'),
                            report.get('pdop'),
                            report.get('uSat', sum(1 for s in sats if s.get('used'))),
                            report.get('nSat', len(sats)),
                        ),
                    )
                    conn.commit()
                    stats.last_sky_write = now
                    stats.sky_written += 1
                # Per-SV observations (GNSS Observatory) on a much coarser throttle.
                # Only positioned sats (az+el present) — an unpositioned sat
                # carries no angular info; same filter the live skyplot applies.
                # All sats in one sample share a timestamp, marking the sweep.
                if now - stats.last_sat_obs_write >= SAT_OBSERVATION_INTERVAL_SECONDS:
                    rows = sky_observation_rows(sats, now_canonical())
                    if rows:
                        conn.executemany(
                            'INSERT INTO sat_observations '
                            '(timestamp, gnssid, svid, az, el, snr, used, health) '
                            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                            rows,
                        )
                        conn.commit()
                        stats.sat_obs_written += len(rows)
                    stats.last_sat_obs_write = now
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
                    if age_seconds(gps_time_str) > GPS_TIME_MAX_AGE_SECONDS:
                        stats.stale_time += 1
                        continue
                except ValueError:
                    pass

            # A usable fix arrived; the stream is alive even if we throttle it.
            last_fix_time = now
            stats.note_fix(lat, lon, now)

            # Motion-gated cadence: full nav rate moving, ~1 Hz parked.
            # Unknown speed errs toward moving so raw never silently loses fixes.
            speed = report.get('speed')
            parked = speed is not None and speed < PARKED_SPEED_MPS
            write_interval = (
                PARKED_WRITE_INTERVAL_SECONDS if parked else MOVING_WRITE_INTERVAL_SECONDS
            )
            if now - last_log_time < write_interval:
                stats.throttled += 1
                continue

            conn.execute(
                'INSERT INTO gps_points '
                '(timestamp, lat, lon, speed, altitude, track, '
                'epx, epy, epv, eps, climb, mode) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    now_canonical(),
                    lat,
                    lon,
                    speed,
                    report.get('alt'),
                    report.get('track'),
                    report.get('epx'),
                    report.get('epy'),
                    report.get('epv'),
                    report.get('eps'),
                    report.get('climb'),
                    mode,
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

    print('GPS logger started', flush=True)
    last_log_time = 0.0
    stats = LoggerStats()

    while True:
        try:
            last_log_time = run_session(conn, last_log_time, stats)
        except KeyboardInterrupt:
            print('GPS logger stopped', flush=True)
            break
        except Exception as e:
            print(f'GPS error: {e}, reconnecting in 5s', file=sys.stderr, flush=True)
            time.sleep(5)


if __name__ == '__main__':
    main()
