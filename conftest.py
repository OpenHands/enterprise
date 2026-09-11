"""Owns the lifetime of the Postgres container the database tests share.

These hooks live at the repo root so the pytest-xdist controller always loads
them. The controller starts the container, hands its address to each worker
through ``workerinput``, and removes it when the run ends. Because the
controller is the process that created it, Ryuk also removes the container if
the run is killed before these hooks get to run.
"""

from __future__ import annotations

import os

import pytest

from tests import postgres_testdb


def pytest_configure(config: pytest.Config) -> None:
    # An xdist worker is handed the address of the controller's container.
    if hasattr(config, 'workerinput') or config.option.collectonly:
        return
    # When POSTGRES_TEST_DATABASE_URL is set, an external Postgres (e.g. the
    # service container in the postgres-integration-tests workflow) is already
    # available and tests connect to it directly. Skip the shared
    # testcontainers server so that run needs neither Docker nor the
    # testcontainers dependency.
    if os.getenv('POSTGRES_TEST_DATABASE_URL'):
        return
    config.stash[postgres_testdb.TEST_SERVER] = postgres_testdb.start_server()


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node) -> None:
    """Hand each xdist worker the address of this run's postgres.

    ``optionalhook`` because pytest-xdist declares this hook, and without it
    registered pytest rejects the whole conftest.
    """
    test_server = node.config.stash.get(postgres_testdb.TEST_SERVER, None)
    if test_server is not None:
        node.workerinput['pg_host'] = test_server.server.host
        node.workerinput['pg_port'] = test_server.server.port


def pytest_unconfigure(config: pytest.Config) -> None:
    test_server = config.stash.get(postgres_testdb.TEST_SERVER, None)
    if test_server is not None:
        test_server.stop()
