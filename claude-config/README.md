# Claude Code config snapshot

A copy of my global Claude Code setup, kept here so I can reproduce it on a new
machine. These files normally live in `~/.claude/`.

## Contents

| File            | Goes to                 | Purpose                                      |
| --------------- | ----------------------- | -------------------------------------------- |
| `CLAUDE.md`     | `~/.claude/CLAUDE.md`   | Global agent instructions (all projects)     |
| `settings.json` | `~/.claude/settings.json` | Model + statusline config                  |
| `statusline.py` | `~/.claude/statusline.py` | Custom status line script                  |

## Restore on a new laptop

```bash
mkdir -p ~/.claude
cp claude-config/CLAUDE.md     ~/.claude/CLAUDE.md
cp claude-config/settings.json ~/.claude/settings.json
cp claude-config/statusline.py ~/.claude/statusline.py
```

`settings.json` points the status line at `python3 ~/.claude/statusline.py`, so
keep that path. Requires `python3` on PATH.

## Deliberately excluded

- **Credentials** (`~/.claude/.credentials.json`) — auth tokens; log in fresh
  with `claude` instead.
- **Memory** (`~/.claude/projects/.../memory/`) — contains internal network
  details (IPs, SSH notes). Kept off this public repo; re-create as needed.
- Caches, logs, sessions, history — machine-local, not config.
