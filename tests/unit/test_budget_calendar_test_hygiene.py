"""Guard against date-dependent budget tests that read the real wall clock.

The org-budget cycle logic keys off "now" (see ``_current_cycle_start`` /
``_next_cycle_start`` in ``server.services.org_budget_service``). A test that
asserts on a cycle boundary while reading ``datetime.now()`` only passes on the
days of the month where the calendar happens to line up, so it can go green for
months and then fail CI on, say, the first of a month with no code change.

This guard fails if any budget test asserts on a cycle boundary without pinning
the clock (``@freeze_time(...)`` or ``with freeze_time(...):``). It deliberately
does *not* flag the many legitimate ``datetime.now()`` calls used only to build
fixtures -- only assertions that compare against a computed cycle boundary.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CYCLE_HELPERS = ('_current_cycle_start', '_next_cycle_start')

# Test modules whose cycle-boundary assertions must run under a frozen clock.
GUARDED_TEST_FILES = (Path(__file__).parent / 'test_org_budget_service.py',)


def _freezes_clock(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in func.decorator_list:
        if 'freeze_time' in ast.dump(decorator):
            return True
    for node in ast.walk(func):
        if isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                if 'freeze_time' in ast.dump(item.context_expr):
                    return True
    return False


def _asserts_on_cycle_boundary(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            dumped = ast.dump(node.test)
            if any(helper in dumped for helper in CYCLE_HELPERS):
                return True
    return False


def test_cycle_boundary_assertions_pin_the_clock():
    offenders: list[str] = []
    for path in GUARDED_TEST_FILES:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not node.name.startswith('test_'):
                continue
            if _asserts_on_cycle_boundary(node) and not _freezes_clock(node):
                offenders.append(f'{path.name}::{node.name}')

    assert not offenders, (
        'These budget tests assert on a cycle boundary '
        f'({" / ".join(CYCLE_HELPERS)}) while reading the real wall clock. '
        'Pin the date with @freeze_time(...) or `with freeze_time(...):` so the '
        'assertion is deterministic instead of passing only on certain days of '
        f'the month: {offenders}'
    )


_OFFENDER_SRC = """
def test_unpinned():
    assert settings.cycle_start_at == _next_cycle_start(anchor, reset_day)
"""

_PINNED_DECORATOR_SRC = """
@freeze_time('2026-06-15')
def test_pinned():
    assert settings.cycle_start_at == _next_cycle_start(anchor, reset_day)
"""

_PINNED_WITH_SRC = """
def test_pinned():
    with freeze_time('2026-06-15'):
        assert settings.cycle_start_at == _next_cycle_start(anchor, reset_day)
"""

_FIXTURE_ONLY_SRC = """
def test_no_boundary_assertion():
    started = datetime.now(UTC)
    assert started is not None
"""


def _only_func(src: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    return next(
        node
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    )


def test_guard_detects_an_unpinned_cycle_boundary_assertion():
    offender = _only_func(_OFFENDER_SRC)
    assert _asserts_on_cycle_boundary(offender)
    assert not _freezes_clock(offender)


@pytest.mark.parametrize('src', [_PINNED_DECORATOR_SRC, _PINNED_WITH_SRC])
def test_guard_accepts_a_pinned_clock(src):
    pinned = _only_func(src)
    assert _asserts_on_cycle_boundary(pinned)
    assert _freezes_clock(pinned)


def test_guard_ignores_datetime_now_outside_boundary_assertions():
    assert not _asserts_on_cycle_boundary(_only_func(_FIXTURE_ONLY_SRC))


def test_guard_watches_the_budget_service_tests():
    assert [path.name for path in GUARDED_TEST_FILES] == ['test_org_budget_service.py']
    assert all(path.exists() for path in GUARDED_TEST_FILES)
    assert CYCLE_HELPERS == ('_current_cycle_start', '_next_cycle_start')
