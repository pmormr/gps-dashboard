"""Validate that gpsd is correctly configured and receiving GPS data."""

import json
import os
import re
import socket
import subprocess
import sys
import time


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


def get_configured_device():
    try:
        with open('/etc/default/gpsd') as f:
            content = f.read()
        match = re.search(r'DEVICES="([^"]*)"', content)
        return match.group(1).strip() if match else None
    except FileNotFoundError:
        return None


def check_device(device):
    if not device:
        return False, "No device configured in /etc/default/gpsd"
    exists = os.path.exists(device)
    return exists, f"Device {device} {'exists' if exists else 'not found'}"


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
    """Connect to gpsd and wait for a valid TPV record."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(('127.0.0.1', 2947))
        s.sendall(b'?WATCH={"enable":true,"json":true}\n')
        f = s.makefile('r', encoding='utf-8', errors='replace')
        deadline = time.monotonic() + timeout
        for line in f:
            if time.monotonic() > deadline:
                break
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get('class') == 'TPV' and r.get('lat') is not None:
                mode = r.get('mode', 0)
                lat, lon = r['lat'], r['lon']
                s.close()
                return True, f"TPV received — mode={mode}, lat={lat:.5f}, lon={lon:.5f}"
        s.close()
        return False, f"No TPV record with fix received in {timeout}s"
    except Exception as e:
        return False, f"Socket error: {e}"


def check_fix():
    """Check current fix mode from gpsd."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect(('127.0.0.1', 2947))
        s.sendall(b'?WATCH={"enable":true,"json":true}\n')
        f = s.makefile('r', encoding='utf-8', errors='replace')
        deadline = time.monotonic() + 10
        tpv = sky = None
        for line in f:
            if time.monotonic() > deadline:
                break
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get('class') == 'TPV' and tpv is None:
                tpv = r
            if r.get('class') == 'SKY' and sky is None:
                sky = r
            if tpv and sky:
                break
        s.close()

        mode = (tpv or {}).get('mode', 0)
        mode_names = {0: 'unknown', 1: 'no fix', 2: '2D', 3: '3D'}
        sats = (sky or {}).get('satellites', [])
        used = sum(1 for sat in sats if sat.get('used'))
        visible = len(sats)

        ok = mode >= 2
        return ok, f"Fix mode: {mode_names.get(mode, mode)} | satellites: {used} used / {visible} visible"
    except Exception as e:
        return False, f"Could not query fix: {e}"


def run_all(verbose=True):
    device = get_configured_device()

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
    results = run_all()
    sys.exit(0 if all(ok for _, ok, _ in results) else 1)
