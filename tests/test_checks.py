"""Tests for the shared PASS/FAIL check-runner used by the validate tools."""

from common.checks import run_checks


def test_runs_checks_in_order_and_collects_results():
    checks = [
        ('alpha', lambda: (True, 'ok-a')),
        ('beta', lambda: (False, 'bad-b')),
        ('gamma', lambda: (True, 'ok-g')),
    ]
    results = run_checks(checks, verbose=False)
    assert results == [
        ('alpha', True, 'ok-a'),
        ('beta', False, 'bad-b'),
        ('gamma', True, 'ok-g'),
    ]


def test_empty_check_list():
    assert run_checks([], verbose=False) == []


def test_verbose_prints_per_check_and_all_passed_summary(capsys):
    run_checks([('alpha', lambda: (True, 'ok'))], verbose=True)
    out = capsys.readouterr().out
    assert 'PASS' in out
    assert 'alpha' in out
    assert 'All 1 checks passed.' in out


def test_verbose_summary_reports_partial_pass(capsys):
    checks = [('a', lambda: (True, '')), ('b', lambda: (False, ''))]
    run_checks(checks, verbose=True)
    out = capsys.readouterr().out
    assert 'FAIL' in out
    assert '1/2 checks passed.' in out
