"""Transmit plane — key the Icom ID-5100A for the announce/soundboard console.

The TX sibling of :mod:`radio.recorder`. Phase 3 (R11 in
``plans/radio-platform-plan.md``) is **operator-clicked only**: every transmission
is a ``/radio`` button press by the licensed control op (KC3HEU), so there is no
scheduler and no automatic-control exposure.

This module owns the **never-stuck-keyed invariant**. :func:`keyed_tx` is the one
sanctioned way to assert PTT: it releases in a ``finally`` no matter how the block
exits, and it arms an independent watchdog — on its *own* rigctld connection, so a
playback wedged inside the block while holding the main connection still gets
unkeyed — that force-releases after ``max_seconds``. A stuck transmitter is
illegal, jams the channel, and cooks the finals. Belt-and-suspenders beyond
software: set the rig's own time-out timer (TOT) as a hardware backstop.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from api.rigctld import Rigctld, RigctldError

#: Hard ceiling on how long PTT may stay asserted for one transmission — a hung
#: playback cannot sit on the air longer than this. Env-overridable in the unit.
MAX_TX_SECONDS = float(os.environ.get('GPS_RADIO_MAX_TX_SECONDS', '120'))


def _force_unkey() -> None:
    """Watchdog action: drop PTT over a *fresh* rigctld connection, best-effort.

    Runs on the timer thread, so it must never touch the keyer's own connection —
    independence is the whole point, since the main thread may be wedged inside
    playback still holding its socket. Swallows its own errors (there is nothing
    left to fall back to); the rig's TOT is the hardware last resort.
    """
    try:
        with Rigctld() as rig:
            rig.set_ptt(False)
        print(
            'TX watchdog: force-unkeyed (transmission exceeded the cap)',
            file=sys.stderr,
            flush=True,
        )
    except RigctldError as exc:
        print(f'TX watchdog: force-unkey FAILED: {exc}', file=sys.stderr, flush=True)


@contextmanager
def keyed_tx(max_seconds: float = MAX_TX_SECONDS) -> Iterator[None]:
    """Hold the transmitter keyed for the ``with`` block; guaranteed unkeyed after.

    PTT is asserted on entry and released in a ``finally`` however the block exits
    (return, exception, or the watchdog). If the primary unkey itself fails, an
    independent :func:`_force_unkey` runs over a fresh connection before the
    watchdog is cancelled — the safety net is never dropped on a failed release.

    Args:
        max_seconds: Watchdog ceiling — PTT is force-released if the block runs
            past this, independently of the block's own control flow.

    Yields:
        Control to the caller with the rig on the air.

    Raises:
        RigctldError: If *keying* fails (nothing is left asserted in that case);
            a failed *unkey* is handled internally, not raised.
    """
    watchdog = threading.Timer(max_seconds, _force_unkey)
    with Rigctld() as rig:
        rig.set_ptt(True)
        watchdog.start()
        try:
            yield
        finally:
            try:
                rig.set_ptt(False)
            except RigctldError as exc:
                print(
                    f'TX unkey failed on the primary connection: {exc}; retrying independently',
                    file=sys.stderr,
                    flush=True,
                )
                _force_unkey()
            finally:
                watchdog.cancel()
