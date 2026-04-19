import json
import socket
import sys
import time
from datetime import datetime, timezone

from api.db import get_connection, init_db, migrate

LOG_INTERVAL_SECONDS = 5


def run_session(conn, last_log_time: float) -> float:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(30)
    try:
        sock.connect(('127.0.0.1', 2947))
        sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
        f = sock.makefile('r', encoding='utf-8', errors='replace')
        for line in f:
            try:
                report = json.loads(line)
            except json.JSONDecodeError:
                continue

            if report.get('class') != 'TPV':
                continue
            if report.get('lat') is None or report.get('lon') is None:
                continue

            now = time.monotonic()
            if now - last_log_time < LOG_INTERVAL_SECONDS:
                continue

            timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            conn.execute(
                "INSERT INTO gps_points (timestamp, lat, lon, speed, altitude, track) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    timestamp,
                    report['lat'],
                    report['lon'],
                    report.get('speed'),
                    report.get('alt'),
                    report.get('track'),
                ),
            )
            conn.commit()
            last_log_time = now
    finally:
        sock.close()

    return last_log_time


def main():
    conn = get_connection()
    init_db(conn)
    migrate(conn)

    print("GPS logger started", flush=True)
    last_log_time = 0.0

    while True:
        try:
            last_log_time = run_session(conn, last_log_time)
        except KeyboardInterrupt:
            print("GPS logger stopped", flush=True)
            break
        except Exception as e:
            print(f"GPS error: {e}, reconnecting in 5s", file=sys.stderr, flush=True)
            time.sleep(5)


if __name__ == '__main__':
    main()
