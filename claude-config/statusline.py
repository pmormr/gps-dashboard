#!/usr/bin/env python3
"""Claude Code status line.

Renders a single line beneath the input prompt:

    <folder> · ⎇ <branch>[*] · <model> · <effort> · 5h <n>% · 7d <n>% · $<cost> · <bar> <pct>% <used>/<max>

Reads the session JSON that Claude Code writes to stdin. See:
https://code.claude.com/docs/en/statusline
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

RESET = "\033[0m"
DIM = "\033[2m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"

BAR_WIDTH = 10


def _tokens(n: int) -> str:
    """Format a token count compactly (e.g. 47500 -> '47.5k')."""
    if n >= 1000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return str(n)


def _git_info(cwd: str) -> str | None:
    """Return ``branch`` (with a yellow ``*`` when dirty), or None if not a repo."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--branch"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None

    lines = result.stdout.splitlines()
    branch = lines[0][3:].split("...", 1)[0]  # strip "## ", drop upstream
    dirty = len(lines) > 1
    marker = f"{YELLOW}*{RESET}" if dirty else ""
    return f"{MAGENTA}⎇ {branch}{RESET}{marker}"


def _usage_color(pct: float) -> str:
    """Pick a color that escalates with usage percentage."""
    return GREEN if pct < 50 else YELLOW if pct < 80 else RED


def _bar(pct: float) -> str:
    """Build a colored progress bar; color escalates with usage."""
    filled = max(0, min(BAR_WIDTH, round(pct / 100 * BAR_WIDTH)))
    return f"{_usage_color(pct)}{'▓' * filled}{DIM}{'░' * (BAR_WIDTH - filled)}{RESET}"


def main() -> None:
    """Render the status line from the JSON payload on stdin."""
    data: dict[str, Any] = json.load(sys.stdin)

    model: str = data.get("model", {}).get("display_name", "?")
    effort: str | None = data.get("effort", {}).get("level")
    cwd: str = data.get("workspace", {}).get("current_dir") or data.get("cwd", "")
    cost: float | None = data.get("cost", {}).get("total_cost_usd")

    ctx: dict[str, Any] = data.get("context_window", {})
    used: int = ctx.get("total_input_tokens") or 0
    total: int = ctx.get("context_window_size") or 0
    pct: float = ctx.get("used_percentage") or 0.0

    # Left cluster: folder · branch · model · effort · session/weekly · cost
    folder = Path(cwd).name or cwd or "?"
    left: list[str] = [f"{CYAN}{folder}{RESET}"]
    branch = _git_info(cwd) if cwd else None
    if branch:
        left.append(branch)
    left.append(model)
    if effort:
        left.append(effort)

    rate: dict[str, Any] = data.get("rate_limits", {})
    five_hour: float | None = rate.get("five_hour", {}).get("used_percentage")
    seven_day: float | None = rate.get("seven_day", {}).get("used_percentage")
    if five_hour is not None:
        left.append(f"5h {_usage_color(five_hour)}{five_hour:.0f}%{RESET}")
    if seven_day is not None:
        left.append(f"7d {_usage_color(seven_day)}{seven_day:.0f}%{RESET}")
    if cost is not None:
        left.append(f"{GREEN}${cost:.2f}{RESET}")

    # Context-window bar, inline after the cost.
    ctx_seg = f"{_bar(pct)} {pct:.0f}%"
    if total:
        ctx_seg += f" {_tokens(used)}/{_tokens(total)}"
    left.append(ctx_seg)

    print(f"{DIM} · {RESET}".join(left))


if __name__ == "__main__":
    main()
