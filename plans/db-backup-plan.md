# DB backup to rex-nas — plan

Status: **designed, not started** (2026-07-03). Decisions on target volume + cadence still open — see Open decisions.

Back up `/mnt/nvme/data/gps_history.db` from the van Pi to rex-nas, opportunistically (the van is frequently off-grid; rex-nas is reachable when parked at home via HaLow, or on-grid via WireGuard).

## Facts the design rests on (surveyed 2026-07-03)

- DB: 371 MB, WAL mode, live writers (logger / mqtt-ingest / processor). Pi sqlite3 is **3.40.1** — too old for `sqlite3_rsync` (needs 3.47+ both ends); not worth vendoring binaries at this size.
- The irreplaceable tiers are raw `gps_points` + `receiver_metadata`, `annotations`/`marks`, the sensor `*_readings` tables, and `sat_observations`. Everything else (track/drone/phone tiers) is rebuildable — but at 371 MB total, back up the whole file.
- `gps-drone-sync` is the proven template: timer-driven oneshot that preflights rex-nas SSH reachability and exits 0 when away (boondocking = clean no-op).
- rex-nas is a UGREEN DXP6800 Pro (survey → vault `rex/devices/rex-nas.md`). Two traps found:
  - **`/volume2` pool (md2) is a degraded RAID5** — one member missing, zero redundancy. Don't land backups there until repaired.
  - `/volume1` is the only healthy redundant pool but is 94% full (fine for a few GB of DB history, but watch it).
  - `sudo` on the NAS needs a password → no root-side automation; everything must run as `pmorgan` over SSH.

## Design

1. **Pi-side consistent snapshot** — `sqlite3 gps_history.db ".backup /mnt/nvme/backup/gps_history.snap.db"`.
   - `.backup` is safe against live writers and preserves page layout, so rsync's delta transfer stays cheap. **Not `VACUUM INTO`** — it compacts by rewriting pages, which defeats rsync deltas.
   - The local snapshot doubles as an on-NVMe corruption-recovery point (not disk-loss protection).
2. **rsync over SSH to rex-nas** — `--inplace` against the previous copy; an append-mostly DB transfers only changed pages (a few MB per run — HaLow-friendly).
3. **Retention: `rsync --link-dest` dated dirs on the NAS**, pruned by the same script (e.g. keep 7 daily + 8 weekly). Deliberately *not* UGOS btrfs snapshots: our own rotation is verifiable over plain SSH, survives a UGOS reinstall, and UGOS snapshot scheduling can't even be confirmed without UI access. Each dated dir is a full 371 MB on disk (the file changes every day, hardlinks don't dedupe it) — ~15 copies ≈ 6 GB, noise.
4. **Cadence: 6-hourly** (mirrors drone-sync). Deltas are tiny; shrinks the loss window to hours while home. Away, exposure is trip-length regardless of cadence.
5. **Deliverables**:
   - `tools/backup_db.py` — snapshot + preflight + rsync + prune; Python for the checks/prune logic and the Ctrl+C/exit-130 convention. Preflight failure (NAS unreachable) exits 0 silently, like `import_drone`.
   - `deploy/gps-db-backup.service` + `.timer` (oneshot + `OnUnitActiveSec=6h`, `Persistent=true`). Deploy hook already reinstalls unit files on any `deploy/` change; the timer needs enabling once (check whether the hook's enable step covers new timers or only `gps-drone-sync.timer` — extend it if hardcoded).
   - CLAUDE.md: one line under Deployment (backup path) + the service in the project tree.
   - Tests for the pure helpers (prune-set selection, dated-dir naming).

## Open decisions

- **Target volume**: `/volume1` (healthy RAID5, but 94% full) vs `/volume2` (where van data already lives, but degraded until md2 gets its third member). Leaning `/volume1` now, or gate on the md2 repair.
- **Cadence**: 6 h proposed; daily if NAS-side history should stay tidier.

## Related follow-ups (not this plan)

- **md2 repair on rex-nas** — replace/re-add the missing RAID5 member (UGOS UI; needs physical/console access). Tracked in vault `rex-nas.md` Open questions.
- Identify `10.1.100.147` (the actual UniFi-controller host) — vault `rex-nas.md`.

## Restore procedure (for reference)

Stop writers (`gps-logger`, `mqtt-ingest`, `gps-processor`, `gps-dashboard`), copy the chosen dated copy from the NAS back to `/mnt/nvme/data/gps_history.db` (remove stale `-wal`/`-shm` siblings), restart. `PRAGMA integrity_check;` before trusting it.
