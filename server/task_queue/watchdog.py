"""Local watchdog: bounded self-exit when this process stops making progress.

A hung job keeps Procrastinate's heartbeat healthy, so stalled-worker recovery
never fires, and Docker Compose has no probe-driven restart. The portable
answer is for the process to exit by itself and let the platform restart it;
the abandoned job stays ``doing`` with ``abort_requested`` false, so stalled
recovery re-queues it.

Three threads, none of them on the event loop:

- enforcement reads only in-process state (the execution registry and the
  event-loop heartbeat) and can always reach ``os._exit``. It opens no sockets,
  does no database work and holds no lock that an I/O path could hold.
- telemetry does the logging and database reads that enrich alerts. If it
  blocks, enforcement waits for it for at most ``exit_report_wait`` seconds.
- the probe server answers liveness from a flag enforcement publishes.

Everything is keyed on an in-process registry, never on the queue row's
``worker_id``: after stalled recovery re-queues a job, the replacement worker's
fetch overwrites ``worker_id`` on the same row while this process may still be
running it.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import queue
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import psycopg
from procrastinate import JobContext
from psycopg.rows import dict_row

from openhands.app_server.utils.logger import openhands_logger as logger

WATCHDOG_EXIT_CODE = 70
Clock = Callable[[], float]


class TaskTimeout(Exception):
    """A job exceeded its time budget. Ordinary exception: the job retries.

    Never raised by setting ``abort_requested``: Procrastinate turns an aborted
    ``doing`` job into ``failed`` on retry, destroying the work.
    """


@dataclass(frozen=True)
class Execution:
    kind: str
    ident: str
    attempt: int
    budget_deadline: float
    kill_deadline: float


class ExecutionRegistry:
    """What this process is running right now, with monotonic deadlines."""

    def __init__(self, clock: Clock = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[int, Execution] = {}
        self._tokens = itertools.count()

    def start(
        self, kind: str, ident: str, attempt: int, budget: float, grace: float
    ) -> int:
        now = self._clock()
        entry = Execution(kind, ident, attempt, now + budget, now + budget + grace)
        token = next(self._tokens)
        with self._lock:
            self._entries[token] = entry
        return token

    def finish(self, token: int) -> None:
        with self._lock:
            self._entries.pop(token, None)

    def snapshot(self) -> dict[int, Execution]:
        with self._lock:
            return dict(self._entries)


class LoopHeartbeat:
    """Touched from the event loop; read by enforcement to detect a freeze."""

    def __init__(self, clock: Clock = time.monotonic):
        self._clock = clock
        self.last = clock()

    def touch(self) -> None:
        self.last = self._clock()

    async def run(self, interval: float) -> None:
        while True:
            self.touch()
            await asyncio.sleep(interval)


JobLookup = Callable[[str], dict[str, Any]]


class QueueRowLookup:
    """Reads a job's queue row for alerts. Used only by the telemetry thread.

    Timeouts and keepalives are hygiene here, not the guarantee: enforcement
    never waits on this.
    """

    def __init__(self, conninfo: str):
        self._conninfo = conninfo
        self._conn: psycopg.Connection | None = None

    def __call__(self, job_id: str) -> dict[str, Any]:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(
                self._conninfo,
                autocommit=True,
                options='-c statement_timeout=5000',
                row_factory=dict_row,
            )
        try:
            row = self._conn.execute(
                'SELECT status, worker_id, attempts, abort_requested '
                'FROM procrastinate_jobs WHERE id = %s',
                (int(job_id),),
            ).fetchone()
        except Exception:
            self._conn.close()
            self._conn = None
            raise
        return dict(row) if row else {}


class Telemetry:
    """Logs watchdog events off the enforcement path, enriched from the DB."""

    def __init__(self, lookup: JobLookup | None = None):
        self._lookup = lookup
        self._events: queue.SimpleQueue[tuple[str, dict[str, Any], threading.Event]] = (
            queue.SimpleQueue()
        )
        self._thread = threading.Thread(
            target=self._run, name='task-queue-telemetry', daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def report(self, event: str, **fields: Any) -> threading.Event:
        done = threading.Event()
        self._events.put((event, fields, done))
        return done

    def _run(self) -> None:
        while True:
            event, fields, done = self._events.get()
            try:
                if self._lookup and fields.get('kind') == 'job':
                    try:
                        fields['queue_row'] = self._lookup(fields['ident'])
                    except Exception as error:
                        fields['queue_row_error'] = repr(error)
                logger.error(event, extra={'watchdog': fields})
            finally:
                done.set()


class Watchdog:
    def __init__(
        self,
        registry: ExecutionRegistry,
        heartbeat: LoopHeartbeat,
        telemetry: Telemetry,
        *,
        check_interval: float,
        loop_stall_timeout: float,
        exit_report_wait: float = 1.0,
        exit_fn: Callable[[int], Any] = os._exit,
        clock: Clock = time.monotonic,
    ):
        self._registry = registry
        self._heartbeat = heartbeat
        self._telemetry = telemetry
        self._check_interval = check_interval
        self._loop_stall_timeout = loop_stall_timeout
        self._exit_report_wait = exit_report_wait
        self._exit_fn = exit_fn
        self._clock = clock
        self._reported_overdue: set[int] = set()
        self._stop = threading.Event()
        self._exiting = False
        self.last_tick = clock()
        self._thread = threading.Thread(
            target=self._enforce, name='task-queue-watchdog', daemon=True
        )

    def start(self) -> None:
        self._telemetry.start()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def healthy(self) -> bool:
        stale_after = 3 * self._check_interval
        return not self._exiting and self._clock() - self.last_tick < stale_after

    def check(self) -> dict[str, Any] | None:
        """One enforcement pass; returns the reason to exit, if any."""
        now = self._clock()
        self.last_tick = now
        stalled_for = now - self._heartbeat.last
        if stalled_for > self._loop_stall_timeout:
            return {'reason': 'event_loop_stalled', 'stalled_seconds': stalled_for}

        entries = self._registry.snapshot()
        self._reported_overdue &= entries.keys()
        for token, entry in entries.items():
            if now >= entry.kill_deadline:
                return {
                    'reason': 'execution_past_grace',
                    'kind': entry.kind,
                    'ident': entry.ident,
                    'attempt': entry.attempt,
                    'overdue_seconds': now - entry.budget_deadline,
                }
            if now >= entry.budget_deadline and token not in self._reported_overdue:
                self._reported_overdue.add(token)
                self._telemetry.report(
                    'task_queue.watchdog_overdue',
                    kind=entry.kind,
                    ident=entry.ident,
                    attempt=entry.attempt,
                    overdue_seconds=now - entry.budget_deadline,
                )
        return None

    def _enforce(self) -> None:
        while not self._stop.wait(self._check_interval):
            reason = self.check()
            if reason is not None:
                self._exiting = True
                self._telemetry.report('task_queue.watchdog_exit', **reason).wait(
                    self._exit_report_wait
                )
                self._exit_fn(WATCHDOG_EXIT_CODE)
                return


def execution_middleware(
    registry: ExecutionRegistry,
    budget_for: Callable[[str], float],
    grace: float,
) -> Callable[..., Awaitable[Any]]:
    """Worker middleware: register each job and enforce its budget in-loop."""

    async def middleware(
        call_next: Callable[[], Awaitable[Any]], context: JobContext, worker: Any
    ) -> Any:
        job = context.job
        budget = budget_for(job.task_name)
        token = registry.start('job', str(job.id), job.attempts, budget, grace)
        try:
            async with asyncio.timeout(budget):
                return await call_next()
        except TimeoutError as error:
            raise TaskTimeout(f'{job.task_name} exceeded {budget}s') from error
        finally:
            registry.finish(token)

    return middleware


class ProbeServer:
    """Liveness endpoint that only reads the flag enforcement publishes."""

    def __init__(self, port: int, watchdog: Watchdog):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != '/healthz':
                    status = 404
                else:
                    status = 200 if watchdog.healthy() else 503
                self.send_response(status)
                self.send_header('Content-Length', '0')
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name='task-queue-probe', daemon=True
        )

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
