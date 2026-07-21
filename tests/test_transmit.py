"""Tests for the transmit safety rail (``radio/transmit.py``).

The never-stuck-keyed invariant is the load-bearing property here: PTT must be
released however the ``with`` block exits, and the watchdog must force-release a
block that runs long — over its own connection, since the point is independence
from a wedged caller. ``Rigctld`` is faked at the module boundary so every
``set_ptt`` (the keyer's and the watchdog's) lands in one ordered event list.
"""

from __future__ import annotations

import time

import pytest

from api.rigctld import RigctldError
from radio import transmit


def make_fake_rig(events: list[object], fail_unkey: bool = False) -> type:
    """A ``Rigctld`` stand-in whose ``set_ptt`` calls append to ``events``.

    Args:
        events: Shared ordered log; ``True`` = key, ``False`` = unkey.
        fail_unkey: When set, every unkey records then raises, to exercise the
            independent-retry path.
    """

    class FakeRig:
        def __init__(self, *_a: object, **_k: object) -> None: ...

        def __enter__(self) -> FakeRig:
            return self

        def __exit__(self, *_a: object) -> bool:
            return False

        def set_ptt(self, on: bool) -> None:
            events.append(bool(on))
            if not on and fail_unkey:
                raise RigctldError('unkey refused')

    return FakeRig


def test_keys_then_unkeys_on_normal_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []
    monkeypatch.setattr(transmit, 'Rigctld', make_fake_rig(events))
    with transmit.keyed_tx():
        events.append('body')
    assert events == [True, 'body', False]


def test_unkeys_when_block_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []
    monkeypatch.setattr(transmit, 'Rigctld', make_fake_rig(events))
    with pytest.raises(ValueError):
        with transmit.keyed_tx():
            raise ValueError('playback blew up')
    assert events == [True, False]  # released despite the failure


def test_failed_primary_unkey_retries_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []
    monkeypatch.setattr(transmit, 'Rigctld', make_fake_rig(events, fail_unkey=True))
    with transmit.keyed_tx():  # instant block; watchdog never fires
        pass
    # key, primary unkey (raises), then the independent _force_unkey retry
    assert events == [True, False, False]


def test_watchdog_force_unkeys_while_block_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []
    monkeypatch.setattr(transmit, 'Rigctld', make_fake_rig(events))
    with transmit.keyed_tx(max_seconds=0.05):
        time.sleep(0.3)
        # The watchdog (0.05 s) has fired independently of this block's exit.
        assert False in events, 'watchdog should have force-unkeyed mid-block'
    assert events[0] is True
