"""Owns the lifetime of the Postgres container the database tests share.

These hooks live at the repo root so the pytest-xdist controller always loads
them. The controller outlives every worker, which makes it the only process
that can safely remove a container they all share. Workers are handed the
run's token through ``workerinput`` and start the container themselves on
first use, so a run that never touches a database never starts one.
"""

from __future__ import annotations

import pytest

from tests import postgres_testdb


def pytest_configure(config: pytest.Config) -> None:
    # An xdist worker inherits the token from the controller instead.
    if hasattr(config, 'workerinput') or config.option.collectonly:
        return
    config.stash[postgres_testdb.RUN_TOKEN] = postgres_testdb.new_run_token()


def pytest_configure_node(node) -> None:
    """Hand each xdist worker the token identifying this run's container."""
    token = node.config.stash.get(postgres_testdb.RUN_TOKEN, None)
    if token is not None:
        node.workerinput['pg_run_token'] = token


def pytest_unconfigure(config: pytest.Config) -> None:
    token = config.stash.get(postgres_testdb.RUN_TOKEN, None)
    if token is not None:
        postgres_testdb.remove_server(token)
