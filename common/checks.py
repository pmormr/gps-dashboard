"""A tiny pass/fail check-runner shared by the gpsd and ntp validate tools.

Each validator defines its own list of named checks; this runs them, prints the
PASS/FAIL report, and returns the structured results so ``__main__`` can derive
an exit code. Deliberately print-based (not ``logging``) to preserve the tools'
existing CLI output verbatim.
"""

from __future__ import annotations

from collections.abc import Callable

Check = tuple[str, Callable[[], tuple[bool, str]]]
Result = tuple[str, bool, str]


def run_checks(checks: list[Check], verbose: bool = True) -> list[Result]:
    """Run named checks and, when verbose, print a PASS/FAIL report and summary.

    Args:
        checks: ``(name, fn)`` pairs where ``fn() -> (ok, message)``.
        verbose: When True, print each check's status + message and a final
            ``passed/total`` summary line.

    Returns:
        ``(name, ok, message)`` triples in check order.
    """
    results: list[Result] = []
    for name, fn in checks:
        ok, msg = fn()
        results.append((name, ok, msg))
        if verbose:
            print(f'{"  PASS" if ok else "  FAIL"}  {name}')
            print(f'        {msg}')

    if verbose:
        passed = sum(1 for _, ok, _ in results if ok)
        total = len(results)
        print()
        print(
            f'All {total} checks passed.' if passed == total else f'{passed}/{total} checks passed.'
        )

    return results
