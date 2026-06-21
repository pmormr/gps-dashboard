"""Validate that gpsd is correctly configured and receiving GPS data."""

import os
import socket
import subprocess
import sys

from common.gpsd import configured_gpsd_device, query_gpsd


def check_service():
    try:
        r = subprocess.run(['systemctl', 'is-active', 'gpsd'],
                           capture_output=True, text=True, timeout=5)
        state = r.stdout.strip()
        return state == 'active', f"gpsd service is {state}"
    except FileNotFoundError:
        return False, "systemctl not found (not running on systemd?)"
    except Exception as e:
        return False, str(e)


def check_device(device):
    if not device:
        return False, "No device configured in /etc/default/gpsd"
    devices = device.split()
    missing = [d for d in devices if not os.path.exists(d)]
    present = [d for d in devices if os.path.exists(d)]
    if not present:
        return False, f"No configured devices found: {', '.join(missing)}"
    msg = f"Present: {', '.join(present)}"
    if missing:
        msg += f" | Missing: {', '.join(missing)}"
    return True, msg


def check_port():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(('127.0.0.1', 2947))
        s.close()
        return True, "Port 2947 accepting connections"
    except ConnectionRefusedError:
        return False, "Port 2947 refused (gpsd not listening?)"
    except Exception as e:
        return False, f"Port 2947 error: {e}"


def check_data_flow(timeout=10):
    """Connect to gpsd and confirm a TPV carrying a position fix is flowing."""
    gpsd = query_gpsd(timeout)
    if not gpsd['connected']:
        return False, "Could not connect to gpsd on 127.0.0.1:2947"
    tpv = gpsd['tpv']
    if tpv.get('lat') is not None:
        return True, (f"TPV received — mode={tpv.get('mode', 0)}, "
                      f"lat={tpv['lat']:.5f}, lon={tpv['lon']:.5f}")
    return False, f"No TPV record with fix received in {timeout}s"


def check_fix():
    """Check current fix mode and satellite usage from gpsd."""
    gpsd = query_gpsd(10)
    if not gpsd['connected']:
        return False, "Could not query fix: gpsd connection failed"
    mode = gpsd['tpv'].get('mode', 0)
    mode_names = {0: 'unknown', 1: 'no fix', 2: '2D', 3: '3D'}
    sats = gpsd['sky'].get('satellites', [])
    used = sum(1 for sat in sats if sat.get('used'))
    visible = len(sats)
    ok = mode >= 2
    return ok, f"Fix mode: {mode_names.get(mode, mode)} | satellites: {used} used / {visible} visible"


def run_all(verbose=True):
    device = configured_gpsd_device()

    checks = [
        ("gpsd service active",    check_service),
        ("device configured",      lambda: check_device(device)),
        ("port 2947 open",         check_port),
        ("data flowing",           check_data_flow),
        ("GPS fix acquired",       check_fix),
    ]

    results = []
    for name, fn in checks:
        ok, msg = fn()
        results.append((name, ok, msg))
        if verbose:
            status = '  PASS' if ok else '  FAIL'
            print(f"{status}  {name}")
            print(f"        {msg}")

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)

    if verbose:
        print()
        if passed == total:
            print(f"All {total} checks passed.")
        else:
            print(f"{passed}/{total} checks passed.")

    return results


if __name__ == '__main__':
    try:
        results = run_all()
        sys.exit(0 if all(ok for _, ok, _ in results) else 1)
    except KeyboardInterrupt:
        print('\nInterrupted.')
        sys.exit(130)
