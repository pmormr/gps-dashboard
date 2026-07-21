"""Shared last-known rig-frequency store, for inferring Repeater-Mode freqs.

CI-V reads are NAK'd (``RPRT -9``) while the rig is in cross-band Repeater Mode, so
a live ``rig_snapshot()`` comes back empty and both the recorder (RX captures) and
the transmit console (TX rows) would log a blank freq. But the freq can't change
without exiting the mode — which restores CI-V and refreshes the read — so the
frozen last-online freq *is* the true repeater freq, and the transmission is on
both bands at once.

The catch is cross-process: the web app polls the freq (``status``) and knows the
staged A/B pair (``stage_crossband``), but the **recorder is a separate process**
and can't see the web app's memory. So this state lives in one DB row
(``radio_freq_state``), written by whichever process last read a live freq and by
the web app when it stages a pair, and read back by both when a live read fails.
"""

from __future__ import annotations

from sqlite3 import Connection


def remember_main(conn: Connection, freq_hz: int | None, mode: str | None) -> None:
    """Record a live main-band read; a ``None`` freq (unreadable) is ignored.

    Write-on-change: a read matching the stored value is skipped, so the common
    steady-state poll does no DB write.

    Args:
        conn: Open connection to the main DB.
        freq_hz: The main-band frequency just read, or ``None``.
        mode: The main-band mode just read.
    """
    if freq_hz is None:
        return
    row = conn.execute(
        'SELECT last_freq_hz, last_mode FROM radio_freq_state WHERE id = 1'
    ).fetchone()
    if row is not None and row['last_freq_hz'] == freq_hz and row['last_mode'] == mode:
        return
    conn.execute(
        'INSERT INTO radio_freq_state (id, last_freq_hz, last_mode) VALUES (1, ?, ?) '
        'ON CONFLICT(id) DO UPDATE SET last_freq_hz = excluded.last_freq_hz, '
        'last_mode = excluded.last_mode',
        (freq_hz, mode),
    )
    conn.commit()


def remember_staged(conn: Connection, main_hz: int, other_hz: int) -> None:
    """Record the last cross-band staged pair (the main band + the other band).

    Args:
        conn: Open connection to the main DB.
        main_hz: The frequency of the band left as Main.
        other_hz: The frequency of the other (sub) band.
    """
    conn.execute(
        'INSERT INTO radio_freq_state (id, staged_main_hz, staged_other_hz) VALUES (1, ?, ?) '
        'ON CONFLICT(id) DO UPDATE SET staged_main_hz = excluded.staged_main_hz, '
        'staged_other_hz = excluded.staged_other_hz',
        (main_hz, other_hz),
    )
    conn.commit()


def infer_freq(conn: Connection) -> tuple[int | None, str | None, int | None]:
    """Best guess ``(freq_hz, mode, freq_b_hz)`` when a live read failed (Repeater Mode).

    Returns the frozen last-online main freq/mode, plus the staged pair's other
    band only when that pair's main still matches the frozen freq (so a stale pair
    from before a retune is dropped). All ``None`` before anything is recorded.

    Args:
        conn: Open connection to the main DB.
    """
    row = conn.execute(
        'SELECT last_freq_hz, last_mode, staged_main_hz, staged_other_hz '
        'FROM radio_freq_state WHERE id = 1'
    ).fetchone()
    if row is None:
        return None, None, None
    staged_main = row['staged_main_hz']
    pair_current = staged_main is not None and staged_main == row['last_freq_hz']
    other = row['staged_other_hz'] if pair_current else None
    return row['last_freq_hz'], row['last_mode'], other
