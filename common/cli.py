"""CLI entrypoint helpers enforcing the project's Ctrl+C convention.

Every ``tools/`` script must turn a Ctrl+C into a clean ``Interrupted.`` message
and exit 130 rather than dumping a traceback (see CLAUDE.md). These wrappers keep
that in one place: :func:`run_cli` for plain entrypoints, :func:`run_click` for
click commands that also need to tell a Ctrl+C apart from a declined prompt.

A tool that fans work out over a ``ThreadPoolExecutor`` still handles Ctrl+C
*inside* its ``as_completed`` loop (cancel pending futures, print partial stats);
that inner handler exits first, and these wrappers are the outer safety net.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

INTERRUPTED_EXIT = 130


def run_cli(entry: Callable[[], int | None]) -> None:
    """Run a plain entrypoint, mapping Ctrl+C to a clean exit 130.

    Args:
        entry: A zero-arg callable. If it returns an int, that becomes the
            process exit code; ``None`` exits 0. A ``SystemExit`` it raises
            (e.g. from a click command in standalone mode) propagates unchanged.
    """
    try:
        code = entry()
    except KeyboardInterrupt:
        print('\nInterrupted.', file=sys.stderr)
        sys.exit(INTERRUPTED_EXIT)
    sys.exit(code or 0)


def run_click(command: Callable) -> None:
    """Run a click command with explicit Ctrl+C / abort / error handling.

    Invokes the command with ``standalone_mode=False`` so a Ctrl+C (an
    ``Abort`` chained from ``KeyboardInterrupt``) is distinguishable from a
    declined confirmation ('n' at a prompt → a bare ``Abort``): Ctrl+C exits 130,
    while a declined prompt or any ``ClickException`` exits as click normally
    would.

    Args:
        command: The click command object to invoke.
    """
    import click

    try:
        command(standalone_mode=False)
    except click.exceptions.Abort as exc:
        if isinstance(exc.__cause__, KeyboardInterrupt):
            click.echo('\nInterrupted.', err=True)
            sys.exit(INTERRUPTED_EXIT)
        click.echo('Aborted.', err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        click.echo('\nInterrupted.', err=True)
        sys.exit(INTERRUPTED_EXIT)
    except click.exceptions.ClickException as exc:
        exc.show()
        sys.exit(exc.exit_code)
