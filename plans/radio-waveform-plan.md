# Radio transmission waveform preview — plan

A small waveform preview for each recorded transmission, derived at record time
and stored on the row. Interactive in the player (playhead cursor synced to the
`<audio>` element + click-to-seek), plus a thin static strip on the collapsed log
row so the log is scannable at a glance.

Rides on `radio-recorder` + `/api/radio/transmissions` + `Radio.svelte`. Folds
into `.claude/modules/frontend.md` (player UI) + the recorder docstring when it
lands; drop this file then.

## Why record-time derivation (vs. server-on-demand / client decode)

The recorder already computes a per-block `peak` in `block_energy()` and throws
the per-block detail away — the envelope data is already flowing through the
gate, so building it is nearly free and needs no WAV re-decode. Storing it **on
the row** means it **survives retention pruning**: the pruner deletes WAVs after
180 d / 4 GB but keeps the row, so a server-recompute or client-decode approach
would leave the older half of the log visually blank. It's also zero client-side
decode, which matters for phone clients on the van LAN.

## Decisions (locked)

- **Source:** record-time derivation, stored compact on the row.
- **Interactive:** playhead cursor synced to `<audio>.currentTime` + click-to-seek.
- **Placement:** thin static strip on the collapsed row **and** a larger
  interactive waveform in the expanded player.
- **Normalization:** absolute (fixed reference), **not** per-clip — a quieter
  talker reads shorter across the whole log.
- **Encoding:** fixed **dBFS window** → 0..255 (not linear amplitude). Ceiling
  0 dBFS; silence → 0. Absolute/cross-clip-comparable, but spreads the real
  signal range across the full bar height so voice is legible instead of stumpy.
  - **Floor is unit-tunable:** dedicated `GPS_RADIO_WAVEFORM_FLOOR_DBFS`
    (default **−64**), the way `OPEN_DBFS`/`CLOSE_DBFS` are — the capture-path
    floor already moved once (−50 → −64 on 2026-07-21, USB-ground-isolator;
    `tools/radio_floor.py` measures it), so don't freeze a magic number. For
    reference the gate currently opens −52 / closes −56 dBFS; at a −64 floor,
    voice ≈ −10 dBFS → ~84% height, inter-word gaps near the floor → low but
    nonzero texture.
- **Format:** fixed-N JSON int array in a `TEXT` column; SVG bars.
- **Rollout:** no migration code kept in the repo — only fresh-init DDL. The live
  Pi table is ALTERed once by hand and existing rows backfilled by a throwaway
  script; neither is committed.

## Deferred to implementation

- **Row strip layout** — thin full-width second line under the row vs. a small
  inline fixed-width block. Settle against the live mobile view, not by guessing.

## Architecture

### Envelope — shared pure code (permanent)

`radio/waveform.py`: `build_envelope(peaks: Sequence[int], buckets: int) -> list[int]`
— max-in-bin resample of the per-block peaks to N buckets, then absolute encode
each bucket to 0..255 (dBFS window; reuses `vox.amplitude_dbfs`). Captures
shorter than N blocks (< ~9.6 s) yield `< N` buckets — the renderer handles
variable length; no upsample/pad. `WAVEFORM_BUCKETS ≈ 96`. Pure ⇒ unit-tested.

### Recorder (permanent)

- `Capture` accumulates a `peaks: list[int]` — one entry per block, appended in
  `write()` (covers pre-roll + live blocks). ≤ 3000 entries at the 5-min cap ≈
  ~24 KB; negligible.
- `close()`: `build_envelope(self.peaks, WAVEFORM_BUCKETS)` → `json.dumps` → new
  `waveform` column in the INSERT.

### Schema (permanent, fresh-init only)

Add `waveform TEXT` to the `CREATE TABLE radio_transmissions` DDL in `api/db.py`.
No `ALTER` lands in the repo (see Rollout).

### API (permanent)

- Add `waveform` to `_TX_COLUMNS` in `api/routes/radio.py`.
- The list route decodes the JSON string → real array per row, so the payload
  carries `waveform: number[] | null`. No new endpoint — it rides the existing
  `/api/radio/transmissions` list (~a few hundred bytes/row, trivial over LAN).

### Frontend (permanent)

- `web/src/lib/radio.ts`: pure helpers — `parseWaveform`, `cursorX(t, dur, w)`,
  `seekTime(x, w, dur)`. Pure ⇒ unit-tested.
- Waveform render (SVG bars): played bars in accent, unplayed dimmed, split at
  the cursor; click → `audio.currentTime = seekTime(...)`. Likely a small
  `WaveformPlayer.svelte` wrapping the existing `<audio>` + a presentational
  strip reused by the collapsed row.
- Player: replace the raw `<audio controls>` in the expanded detail with the
  interactive waveform + audio.
- Collapsed row: thin static strip (layout TBD during impl — thin full-width
  second line vs. inline fixed-width; mobile-first).
- `web/src/lib/api.ts`: add `waveform` to the `RadioTransmission` type.

### Tests

- `build_envelope`: resample + absolute encode; short-capture (< N blocks) path.
- `cursorX` / `seekTime` math.
- API read path: `waveform` present + decoded in the `/api/radio/transmissions`
  payload (Flask client, temp DB).

## Rollout (throwaway — nothing committed)

1. **ALTER the live table first**, before pushing the writer (else the recorder's
   new INSERT fails on the old table):
   `ssh pmorgan@192.168.42.178 "sqlite3 /mnt/nvme/data/gps_history.db 'ALTER TABLE radio_transmissions ADD COLUMN waveform TEXT'"`
2. Build + commit `static/dist/`.
3. `git push all main` — the hook restarts `radio-recorder` (writer) + `gps-dashboard`.
4. Backfill existing un-pruned rows: a scratchpad script that reads each WAV in
   `BLOCK_FRAMES` chunks, reuses `build_envelope`, and fills `waveform` — `scp`'d
   to the Pi, run once with the Pi's `uv`, then deleted. Pruned rows (audio gone)
   stay flat.
5. Verify on device.

## Landing

Fold the player/waveform detail into `.claude/modules/frontend.md`, note the
`waveform` column in the recorder docstring + CLAUDE.md's `radio_transmissions`
line, and drop this plan file.
