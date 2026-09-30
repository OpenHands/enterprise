from __future__ import annotations

import socket
import threading
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace

import psycopg
import pytest

from server.task_queue import tasks
from server.task_queue.config import Role, Settings
from server.task_queue.jobs import JOBS, ScheduledJob
from server.task_queue.watchdog import LoopHeartbeat
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


class BlackholeProxy:
    """TCP proxy that can stop forwarding while keeping both sockets open."""

    def __init__(self, target: tuple[str, int]):
        self._target = target
        self._listener = socket.create_server(('127.0.0.1', 0))
        self.port = self._listener.getsockname()[1]
        self.blackholed = threading.Event()
        self._sockets: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            upstream = socket.create_connection(self._target)
            self._sockets += [client, upstream]
            for src, dst in ((client, upstream), (upstream, client)):
                threading.Thread(
                    target=self._pump, args=(src, dst), daemon=True
                ).start()

    def _pump(self, src: socket.socket, dst: socket.socket) -> None:
        while True:
            try:
                data = src.recv(65536)
            except OSError:
                return
            if not data:
                return
            if not self.blackholed.is_set():
                dst.sendall(data)

    def close(self) -> None:
        self._listener.close()
        for sock in self._sockets:
            sock.close()


@pytest.fixture
def blackhole(
    test_database: postgres_testdb.TestDatabase,
) -> Iterator[tuple[BlackholeProxy, str]]:
    """A proxy to the test database, and a conninfo that goes through it."""
    server = test_database.server
    proxy = BlackholeProxy((server.host, server.port))
    conninfo = psycopg.conninfo.make_conninfo(
        host='127.0.0.1',
        port=proxy.port,
        dbname=test_database.name,
        user=server.user,
        password=server.password,
    )
    yield proxy, conninfo
    proxy.close()


@pytest.fixture
def live_heartbeat() -> Iterator[LoopHeartbeat]:
    """A loop heartbeat that keeps being touched: the event loop is healthy."""
    heartbeat = LoopHeartbeat()
    stop = threading.Event()

    def touch() -> None:
        while not stop.wait(0.05):
            heartbeat.touch()

    threading.Thread(target=touch, daemon=True).start()
    yield heartbeat
    stop.set()
