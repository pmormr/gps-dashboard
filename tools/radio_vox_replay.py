"""Re-apply the VOX commit rule to stored radio captures — report or purge.

Scores every ``radio_transmissions`` row that still has audio by counting its
WAV's loud blocks (100 ms RMS at/above the gate's open threshold) and compares
the count against the commit rule (``MIN_LOUD_BLOCKS``). Rows recorded before
the rule existed — or after a threshold retune — can then be re-judged:
``--purge`` deletes the failing rows *and* their WAVs (unlike the retention
pruner, which keeps rows). Slightly permissive vs the live gate: pre-roll loud
blocks count here, so borderline captures survive.

Run on the Pi against the live DB (service can stay up; deletes are
row-at-a-time):

    uv run tools/radio_vox_replay.py --db /mnt/nvme/data/gps_history.db
    uv run tools/radio_vox_replay.py --db ... --purge
"""

from __future__ import annotations

import argparse
import wave
from pathlib import Path

from common.cli import run_cli
from radio.paths import audio_dir
from radio.recorder import BLOCK_FRAMES, MIN_LOUD_BLOCKS, OPEN_DBFS
from radio.vox import block_energy, rms_dbfs


def loud_blocks(path: Path, open_dbfs: float) -> int:
    """Count 100 ms blocks in a WAV at/above the gate's open threshold.

    Args:
        path: The capture WAV (mono S16, any rate — blocks are frame-counted).
        open_dbfs: The loudness threshold in dBFS.

    Returns:
        The number of loud blocks.
    """
    count = 0
    with wave.open(str(path)) as w:
        while True:
            frames = w.readframes(BLOCK_FRAMES)
            if len(frames) < BLOCK_FRAMES * 2:
                return count
            sq, _, n = block_energy(frames)
            if rms_dbfs(sq, n) >= open_dbfs:
                count += 1


def main() -> int:
    """Score all audio-bearing rows against the commit rule; purge on request."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', help='DB path (defaults to GPS_DB_PATH resolution)')
    parser.add_argument(
        '--min-loud',
        type=int,
        default=MIN_LOUD_BLOCKS,
        help=f'commit rule: loud blocks a capture needs to keep (default {MIN_LOUD_BLOCKS})',
    )
    parser.add_argument(
        '--open-dbfs',
        type=float,
        default=OPEN_DBFS,
        help=f'loud threshold in dBFS (default {OPEN_DBFS:g})',
    )
    parser.add_argument('--purge', action='store_true', help='delete failing rows and their WAVs')
    args = parser.parse_args()

    import api.db

    api.db.apply_path_overrides(args.db)
    from api.db import get_connection

    conn = get_connection()
    root = audio_dir()
    rows = conn.execute(
        'SELECT id, started_utc, duration_s, dcd_main, audio_path FROM radio_transmissions '
        'WHERE audio_path IS NOT NULL ORDER BY id'
    ).fetchall()

    keep = discard = missing = 0
    print(f'rule: >= {args.min_loud} blocks at {args.open_dbfs:g} dBFS · {len(rows)} rows\n')
    print(f'{"id":>5} {"started":<24} {"dur":>6} {"dcd":>3} {"loud":>4}  verdict')
    for row in rows:
        path = root / row['audio_path']
        if not path.is_file():
            missing += 1
            print(f'{row["id"]:>5} {row["started_utc"]:<24} — audio file missing, skipped')
            continue
        loud = loud_blocks(path, args.open_dbfs)
        ok = loud >= args.min_loud
        keep += ok
        discard += not ok
        dcd = '—' if row['dcd_main'] is None else str(row['dcd_main'])
        print(
            f'{row["id"]:>5} {row["started_utc"]:<24} {row["duration_s"]:>5.1f}s {dcd:>3} '
            f'{loud:>4}  {"keep" if ok else "DISCARD"}'
        )
        if args.purge and not ok:
            path.unlink()
            conn.execute('DELETE FROM radio_transmissions WHERE id = ?', (row['id'],))
            conn.commit()

    action = 'purged' if args.purge else 'would discard'
    print(f'\nkeep {keep} · {action} {discard} · missing-audio skipped {missing}')
    return 0


if __name__ == '__main__':
    run_cli(main)
