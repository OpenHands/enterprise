from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace

import psycopg
import pytest

from server.task_queue import tasks
from server.task_queue.config import Role, Settings
from server.task_queue.jobs import JOBS, ScheduledJob
from tests import postgres_testdb

ALL_JOBS = frozenset(job.name for job in JOBS)


@pytest.fixture
def conninfo(test_database: postgres_testdb.TestDatabase) -> str:
    server = test_database.server
    return psycopg.conninfo.make_conninfo(
        host=server.host,
        port=server.port,
        dbname=test_database.name,
        user=server.user,
        password=server.password,
        application_name='task-queue-test',
    )


@pytest.fixture
def make_settings(conninfo: str) -> Callable[..., Settings]:
    def _make(**overrides) -> Settings:
        settings = Settings(
            role=Role.BUSINESS,
            conninfo=conninfo,
            scheduling_enabled=True,
            enabled_jobs=ALL_JOBS,
            update_heartbeat_interval=0.5,
            fetch_job_polling_interval=0.2,
            pool_timeout=2.0,
        )
        return replace(settings, **overrides)

    return _make


@pytest.fixture
def job_bodies(monkeypatch: pytest.MonkeyPatch) -> dict[str, Callable]:
    """Stand in for the jobs' ``main()``; the queue mechanics stay real.

    Tests set ``job_bodies[name]`` to the coroutine function a job should run.
    A job without an entry fails the test if it runs.
    """
    bodies: dict[str, Callable[[], Awaitable[None]]] = {}

    def load(self: ScheduledJob) -> Callable[[], Awaitable[None]]:
        return bodies[self.name]

    monkeypatch.setattr(ScheduledJob, 'load', load)
    monkeypatch.setattr(tasks, 'RETRY_WAIT_SECONDS', 0)
    return bodies
