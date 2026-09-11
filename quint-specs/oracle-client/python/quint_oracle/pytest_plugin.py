"""Pytest integration: opens a run per test, named by its nodeid, and reports
the outcome the daemon needs to keep or discard the trace.

Zero-config when quint-oracle is pip-installed (a ``pytest11`` entry point);
vendored copies register it with one line in the top-level conftest.py::

    pytest_plugins = ["quint_oracle.pytest_plugin"]

Inert-safe: the library's import-time rebind covers ``log()`` and friends, but
these hooks are registered with pytest regardless, so each gates on
``enabled()`` explicitly.
"""

import pytest

from . import TestGuard, enabled, register_test

_GUARD: pytest.StashKey[TestGuard] = pytest.StashKey()


def pytest_runtest_setup(item):
    if not enabled():
        return
    item.stash[_GUARD] = register_test(item.nodeid)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    if not enabled():
        return
    guard = item.stash.get(_GUARD, None)
    if guard is None:
        return  # an earlier setup hook failed or skipped before registration
    report = outcome.get_result()
    if report.failed:
        # A failure in any phase — setup, call, or teardown — discards the run.
        guard.set_failed()
    if report.when == 'teardown':
        guard.close()
