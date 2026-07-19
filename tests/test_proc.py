"""Tests for the shared subprocess/systemd helpers.

``run`` and ``service_state`` shell out, so these cover the pure glue: how
``ssh_reachable`` builds its command and maps the return code.
"""

from __future__ import annotations

from common import proc


def test_ssh_reachable_true_on_rc_zero(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_run(cmd, timeout=10):
        captured['cmd'] = cmd
        return 0, '', ''

    monkeypatch.setattr(proc, 'run', fake_run)
    assert proc.ssh_reachable('nas') is True
    cmd = captured['cmd']
    assert cmd[0] == 'ssh'
    assert 'BatchMode=yes' in cmd
    assert 'ConnectTimeout=8' in cmd
    assert cmd[-2:] == ['nas', 'true']


def test_ssh_reachable_false_on_nonzero(monkeypatch):
    monkeypatch.setattr(proc, 'run', lambda cmd, timeout=10: (255, '', 'no route'))
    assert proc.ssh_reachable('nas') is False


def test_ssh_reachable_honors_connect_timeout(monkeypatch):
    captured: dict[str, list[str]] = {}

    def fake_run(cmd, timeout=10):
        captured['cmd'] = cmd
        return 0, '', ''

    monkeypatch.setattr(proc, 'run', fake_run)
    proc.ssh_reachable('nas', connect_timeout=3)
    assert 'ConnectTimeout=3' in captured['cmd']
