"""Validate that chrony is configured correctly and synced to GPS."""

import re
import socket
import subprocess
import sys


def _run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return -1, '', f"Command not found: {cmd[0]}"
    except Exception as e:
        return -1, '', str(e)


def check_service():
    code, out, _ = _run(['systemctl', 'is-active', 'chrony'])
    state = out.strip()
    return state == 'active', f"chrony service is {state}"


def check_gps_source():
    code, out, err = _run(['chronyc', 'sources'])
    if code != 0:
        return False, f"chronyc sources failed: {err.strip()}"
    has_gps = 'GPS' in out
    return has_gps, 'GPS SHM source present' if has_gps else 'GPS SHM source not found in chronyc sources'


def check_pps_source():
    code, out, err = _run(['chronyc', 'sources'])
    if code != 0:
        return False, f"chronyc sources failed: {err.strip()}"
    has_pps = 'PPS' in out
    selected = bool(re.search(r'#\*\s+PPS', out))
    if has_pps and selected:
        return True, 'PPS source present and selected'
    if has_pps:
        return False, 'PPS source present but not yet selected (may need time to lock)'
    return False, 'PPS source not found (GPS-only mode or PPS not configured)'


def check_synced():
    code, out, err = _run(['chronyc', 'tracking'])
    if code != 0:
        return False, f"chronyc tracking failed: {err.strip()}"

    ref_match = re.search(r'Reference ID\s*:\s*\S+\s*\((.+?)\)', out)
    ref = ref_match.group(1) if ref_match else 'unknown'

    if 'Not synchronised' in out:
        return False, 'chrony is not synchronised'
    return True, f"Synchronised to {ref}"


def check_stratum():
    code, out, _ = _run(['chronyc', 'tracking'])
    if code != 0:
        return False, 'Could not run chronyc tracking'
    m = re.search(r'Stratum\s*:\s*(\d+)', out)
    if not m:
        return False, 'Could not determine stratum'
    stratum = int(m.group(1))
    ok = stratum <= 10
    return ok, f"Stratum {stratum}"


def check_offset():
    code, out, _ = _run(['chronyc', 'tracking'])
    if code != 0:
        return True, 'Could not check offset'
    m = re.search(r'System time\s*:\s*([\d.]+)\s*seconds\s*(fast|slow)', out)
    if not m:
        return True, 'Offset not available'
    offset_sec = float(m.group(1))
    direction = m.group(2)
    offset_ms = offset_sec * 1000
    ok = abs(offset_sec) < 1.0
    return ok, f"{offset_ms:.3f} ms {direction} of reference"


def check_ntp_serving():
    try:
        code, out, _ = _run(['ss', '-lnup'])
        serving = ':123' in out
        return serving, 'Port 123 listening (serving NTP to LAN)' if serving else 'Port 123 not listening'
    except Exception as e:
        return False, str(e)


def run_all(verbose=True, check_pps=False):
    checks = [
        ('chrony service active', check_service),
        ('GPS SHM source',        check_gps_source),
        ('chrony synced',         check_synced),
        ('stratum',               check_stratum),
        ('time offset',           check_offset),
        ('NTP serving (port 123)', check_ntp_serving),
    ]
    if check_pps:
        checks.insert(2, ('PPS source selected', check_pps_source))

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
    import argparse
    p = argparse.ArgumentParser(description='Validate chrony NTP configuration')
    p.add_argument('--pps', action='store_true', help='Also check PPS source')
    args = p.parse_args()
    results = run_all(check_pps=args.pps)
    sys.exit(0 if all(ok for _, ok, _ in results) else 1)
