"""Tests for the RTS PTT keyer's never-stuck-keyed invariant (:mod:`radio.ptt`).

The hardware ioctls can't run without a real Digirig, so these drive
:func:`keyed_tx_rts` with an injected fake keyer + fake timer and assert the
lifecycle guarantees: keyed then unkeyed, unkeyed even when the body raises,
nothing left asserted when keying itself fails, and the watchdog clears RTS.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from radio.ptt import keyed_tx_rts


class FakePort:
    """Records key/unkey/close and can be told to fail either toggle."""

    def __init__(self, *, fail_key: bool = False, fail_unkey: bool = False) -> None:
        self.events: list[str] = []
        self._fail_key = fail_key
        self._fail_unkey = fail_unkey

    def key(self) -> None:
        self.events.append('key')
        if self._fail_key:
            raise OSError('key failed')

    def unkey(self) -> None:
        self.events.append('unkey')
        if self._fail_unkey:
            raise OSError('unkey failed')

    def close(self) -> None:
        self.events.append('close')


class FakeTimer:
    """A ``threading.Timer`` stand-in that never spawns a thread."""

    def __init__(self, interval: float, fn: Callable[[], None]) -> None:
        self.interval = interval
        self.fn = fn
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


def _harness() -> tuple[FakePort, list[FakeTimer], dict]:
    """A fake port + a list capturing every timer built, wired into kwargs."""
    port = FakePort()
    timers: list[FakeTimer] = []

    def timer_factory(interval: float, fn: Callable[[], None]) -> FakeTimer:
        t = FakeTimer(interval, fn)
        timers.append(t)
        return t

    kwargs = {'port_factory': lambda _d: port, 'timer_factory': timer_factory}
    return port, timers, kwargs


def test_keys_then_unkeys_and_cancels_watchdog() -> None:
    port, timers, kwargs = _harness()
    with keyed_tx_rts('/dev/x', 30, **kwargs):
        assert port.events == ['key']  # keyed for the duration of the block
    assert port.events == ['key', 'unkey', 'close']
    assert timers[0].started and timers[0].cancelled


def test_unkeys_when_body_raises() -> None:
    port, timers, kwargs = _harness()
    with pytest.raises(ValueError):
        with keyed_tx_rts('/dev/x', 30, **kwargs):
            raise ValueError('boom')
    assert port.events == ['key', 'unkey', 'close']  # released despite the error
    assert timers[0].cancelled


def test_key_failure_leaves_nothing_asserted() -> None:
    port = FakePort(fail_key=True)
    timers: list[FakeTimer] = []

    def timer_factory(interval: float, fn: Callable[[], None]) -> FakeTimer:
        t = FakeTimer(interval, fn)
        timers.append(t)
        return t

    with pytest.raises(OSError, match='key failed'):
        with keyed_tx_rts('/dev/x', 30, port_factory=lambda _d: port, timer_factory=timer_factory):
            pass
    # key attempted, port closed; unkey never called (never keyed); watchdog never started.
    assert port.events == ['key', 'close']
    assert not timers[0].started


def test_watchdog_clears_rts() -> None:
    port, timers, kwargs = _harness()
    with keyed_tx_rts('/dev/x', 30, **kwargs):
        timers[0].fn()  # simulate the watchdog firing mid-transmission
        assert port.events == ['key', 'unkey']  # RTS cleared by the watchdog
    assert port.events[-1] == 'close'


def test_failed_primary_unkey_falls_back(monkeypatch) -> None:
    import radio.ptt as ptt

    port = FakePort(fail_unkey=True)
    forced: list[str] = []
    monkeypatch.setattr(ptt, '_force_unkey_fresh', lambda device: forced.append(device))
    with keyed_tx_rts('/dev/x', 30, port_factory=lambda _d: port, timer_factory=FakeTimer):
        pass
    # primary unkey raised → fresh-open backstop invoked before close.
    assert forced == ['/dev/x']
    assert port.events == ['key', 'unkey', 'close']
