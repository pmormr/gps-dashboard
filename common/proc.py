"""Subprocess and systemd helpers shared by the status pages and setup/validate tools.

A thin, exception-safe wrapper around :func:`subprocess.run` plus the
``systemctl is-active`` query that the gpsd/ntp status routes and validators each
re-implemented. Intentionally narrow: command *mutations* (``sudo systemctl
restart``, ``apt-get install``) stay in the tools that own them, since those need
live output, ``sudo``, and bespoke error handling rather than a captured tuple.
"""

from __future__ import annotations

import subprocess


def run(cmd: list[str], timeout: float = 10) -> tuple[int, str, str]:
    """Run a command with captured output, never raising.

    Args:
        cmd: The command and its arguments.
        timeout: Seconds before the command is killed.

    Returns:
        ``(returncode, stdout, stderr)``. On a missing binary or any other
        failure the return code is ``-1`` and ``stderr`` carries the reason, so
        callers can branch on output without a try/except.
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return -1, '', f'Command not found: {cmd[0]}'
    except Exception as exc:
        return -1, '', str(exc)


def ssh_reachable(host: str, connect_timeout: float = 8) -> bool:
    """Whether a host answers a trivial SSH command non-interactively.

    A quick liveness probe used before opportunistic remote work (the DB backup's
    NAS rsync, the drone footage sync) that must no-op cleanly when the
    destination is off-grid. ``BatchMode=yes`` fails fast instead of prompting for
    credentials.

    Args:
        host: The SSH destination (an ssh_config alias or ``user@host``).
        connect_timeout: Seconds for SSH's own connect timeout.

    Returns:
        True iff ``ssh <host> true`` succeeds.
    """
    opts = ['-o', 'BatchMode=yes', '-o', f'ConnectTimeout={int(connect_timeout)}']
    rc, _, _ = run(['ssh', *opts, host, 'true'], timeout=connect_timeout + 5)
    return rc == 0


def service_state(name: str) -> str:
    """Return ``systemctl is-active`` state for a unit.

    Args:
        name: The systemd unit name.

    Returns:
        The reported state (``'active'``, ``'inactive'``, ``'failed'``, …), or
        ``'unknown'`` when systemctl produced no output (e.g. not on systemd).
    """
    _, out, _ = run(['systemctl', 'is-active', name], timeout=5)
    return out.strip() or 'unknown'


def service_active(name: str) -> bool:
    """Whether a systemd unit is currently active.

    Args:
        name: The systemd unit name.

    Returns:
        True iff :func:`service_state` reports ``'active'``.
    """
    return service_state(name) == 'active'
