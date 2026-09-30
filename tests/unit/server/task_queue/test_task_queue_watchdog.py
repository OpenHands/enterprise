"""The local watchdog's enforcement decisions and thread isolation."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from server.task_queue.watchdog import (
    WATCHDOG_EXIT_CODE,
    ExecutionRegistry,
    LoopHeartbeat,
    ProbeServer,
    QueueRowLookup,
    TaskTimeout,
    Telemetry,
    Watchdog,
    execution_middleware,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class RecordingTelemetry(Telemetry):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, dict]] = []

    def report(self, event: str, **fields) -> threading.Event:
        self.events.append((event, fields))
        done = threading.Event()
        done.set()
        return done


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def _watchdog(clock, registry, heartbeat, telemetry=None, **kw) -> Watchdog:
    return Watchdog(
        registry,
        heartbeat,
        telemetry or RecordingTelemetry(),
        check_interval=kw.pop('check_interval', 5.0),
        loop_stall_timeout=kw.pop('loop_stall_timeout', 60.0),
        clock=clock,
        **kw,
    )


class TestEnforcementDecision:
    def test_nothing_to_do_while_within_budget(self, clock):
        registry = ExecutionRegistry(clock)
        registry.start('job', '1', 0, budget=10, grace=5)
        watchdog = _watchdog(clock, registry, LoopHeartbeat(clock))
        clock.now += 9
        assert watchdog.check() is None

    def test_overdue_is_reported_once_then_exit_after_grace(self, clock):
        registry = ExecutionRegistry(clock)
        registry.start('job', '7', 2, budget=10, grace=5)
        heartbeat = LoopHeartbeat(clock)
        telemetry = RecordingTelemetry()
        watchdog = _watchdog(clock, registry, heartbeat, telemetry)

        clock.now += 11
        heartbeat.touch()
        assert watchdog.check() is None
        assert watchdog.check() is None
        assert [event for event, _ in telemetry.events] == [
            'task_queue.watchdog_overdue'
        ]

        clock.now += 4
        heartbeat.touch()
        reason = watchdog.check()
        assert reason is not None
        assert reason['reason'] == 'execution_past_grace'
        assert (reason['ident'], reason['attempt']) == ('7', 2)

    def test_finished_execution_is_forgotten(self, clock):
        registry = ExecutionRegistry(clock)
        token = registry.start('job', '1', 0, budget=10, grace=5)
        registry.finish(token)
        watchdog = _watchdog(clock, registry, LoopHeartbeat(clock))
        clock.now += 100
        watchdog._heartbeat.touch()
        assert watchdog.check() is None

    def test_loop_stall_fires_with_no_execution_overdue(self, clock):
        registry = ExecutionRegistry(clock)
        registry.start('job', '1', 0, budget=3600, grace=60)
        watchdog = _watchdog(
            clock, registry, LoopHeartbeat(clock), loop_stall_timeout=30
        )
        clock.now += 31
        reason = watchdog.check()
        assert reason is not None
        assert reason['reason'] == 'event_loop_stalled'

    def test_recovery_passes_are_enforced_like_jobs(self, clock):
        registry = ExecutionRegistry(clock)
        registry.start('recovery', 'pass-1', 0, budget=30, grace=10)
        heartbeat = LoopHeartbeat(clock)
        watchdog = _watchdog(clock, registry, heartbeat)
        clock.now += 41
        heartbeat.touch()
        assert watchdog.check()['kind'] == 'recovery'

    def test_health_goes_stale_when_enforcement_stops_ticking(self, clock):
        watchdog = _watchdog(clock, ExecutionRegistry(clock), LoopHeartbeat(clock))
        watchdog.check()
        assert watchdog.healthy()
        clock.now += 16
        assert not watchdog.healthy()


class TestEnforcementThread:
    def test_exits_even_when_telemetry_is_blocked(self):
        """Enforcement waits for a blocked telemetry thread only boundedly."""
        release = threading.Event()
        exited = threading.Event()
        codes: list[int] = []

        def blocked_lookup(job_id: str) -> dict:
            release.wait()
            return {}

        registry = ExecutionRegistry()
        registry.start('job', '1', 0, budget=0.2, grace=0.2)
        heartbeat = LoopHeartbeat()
        keep_alive = threading.Event()

        def touch() -> None:
            while not keep_alive.wait(0.05):
                heartbeat.touch()

        threading.Thread(target=touch, daemon=True).start()
        watchdog = Watchdog(
            registry,
            heartbeat,
            Telemetry(blocked_lookup),
            check_interval=0.05,
            loop_stall_timeout=60,
            exit_report_wait=0.3,
            exit_fn=lambda code: (codes.append(code), exited.set()),
        )
        started = time.monotonic()
        watchdog.start()
        try:
            assert exited.wait(5)
        finally:
            keep_alive.set()
            release.set()
        assert codes == [WATCHDOG_EXIT_CODE]
        assert time.monotonic() - started < 2


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


class TestBlackholedTelemetryConnection:
    def test_enforcement_exits_while_the_lookup_is_stuck_on_the_socket(
        self, test_database
    ):
        server = test_database.server
        proxy = BlackholeProxy((server.host, server.port))
        conninfo = (
            f'host=127.0.0.1 port={proxy.port} dbname={test_database.name} '
            f'user={server.user} password={server.password}'
        )
        lookup = QueueRowLookup(conninfo)
        assert lookup('1') == {}  # connection established through the proxy
        proxy.blackholed.set()

        stuck = threading.Event()

        def observed_lookup(job_id: str) -> dict:
            stuck.set()
            return lookup(job_id)

        exited = threading.Event()
        registry = ExecutionRegistry()
        registry.start('job', '1', 0, budget=0.3, grace=0.5)
        heartbeat = LoopHeartbeat()
        keep_alive = threading.Event()

        def touch() -> None:
            while not keep_alive.wait(0.05):
                heartbeat.touch()

        threading.Thread(target=touch, daemon=True).start()
        watchdog = Watchdog(
            registry,
            heartbeat,
            Telemetry(observed_lookup),
            check_interval=0.05,
            loop_stall_timeout=60,
            exit_report_wait=0.5,
            exit_fn=lambda code: exited.set(),
        )
        watchdog.start()
        try:
            assert stuck.wait(5), 'overdue report never reached the lookup'
            assert exited.wait(5)
        finally:
            keep_alive.set()
            proxy.close()


class TestExecutionMiddleware:
    @staticmethod
    def _context(job_id: int = 1):
        return SimpleNamespace(
            job=SimpleNamespace(id=job_id, attempts=0, task_name='scheduled:x')
        )

    async def test_budget_exceeded_raises_task_timeout(self):
        registry = ExecutionRegistry()
        middleware = execution_middleware(registry, lambda _: 0.05, grace=1)

        async def slow() -> None:
            await asyncio.sleep(5)

        with pytest.raises(TaskTimeout):
            await middleware(slow, self._context(), None)
        assert registry.snapshot() == {}

    async def test_registers_the_execution_while_it_runs(self):
        registry = ExecutionRegistry()
        middleware = execution_middleware(registry, lambda _: 10, grace=1)
        seen: list = []

        async def body() -> str:
            seen.extend(registry.snapshot().values())
            return 'ok'

        assert await middleware(body, self._context(42), None) == 'ok'
        assert [(e.kind, e.ident) for e in seen] == [('job', '42')]
        assert registry.snapshot() == {}


class TestProbe:
    def _get(self, port: int, path: str) -> int:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}') as r:
                return r.status
        except urllib.error.HTTPError as error:
            return error.code

    def test_probe_reflects_enforcement_health(self, clock):
        watchdog = _watchdog(clock, ExecutionRegistry(clock), LoopHeartbeat(clock))
        watchdog.check()
        probe = ProbeServer(0, watchdog)
        probe.start()
        try:
            assert self._get(probe.port, '/healthz') == 200
            assert self._get(probe.port, '/other') == 404
            clock.now += 60
            assert self._get(probe.port, '/healthz') == 503
        finally:
            probe.stop()
