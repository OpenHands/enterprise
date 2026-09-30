"""Ops recovery: capped stalled-job recovery and retention, as a supervised loop.

Recovery is deliberately not a Procrastinate task. A fetch refuses any job
whose ``lock`` matches a ``doing`` job, so a locked recovery task whose worker
died mid-run would block every later occurrence of itself, and the only thing
that could clear it is the task that can no longer run.

Instead each ops worker runs this loop on a thread beside the watchdog:

- Each pass is one bounded transaction holding a transaction-scoped advisory
  lock, so replicas coordinate under every pooling mode (PgBouncer transaction
  pooling does not support session advisory locks). A replica that finds the
  lock taken skips the pass.
- Each pass registers a deadline with the watchdog's execution registry, so a
  pass that hangs with a healthy event loop still ends in a bounded self-exit.
- Every candidate is re-read ``FOR UPDATE`` and revalidated before it is
  touched. ``procrastinate_retry_job_v2`` matches any ``doing`` or ``failed``
  job regardless of worker or attempt, so acting on a stale selection could
  requeue a healthy execution that a replacement worker has since fetched.

Only Class A queues are recovered here; per-row work is recovered by its own
row protocol and is excluded by the queue filter.
"""

from __future__ import annotations

import enum
import itertools
import threading
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

from openhands.app_server.utils.logger import openhands_logger as logger
from server.task_queue.config import MAX_ATTEMPTS, Role
from server.task_queue.watchdog import ExecutionRegistry

RECOVERY_LOCK_KEY = 949106549905629328
RECOVERABLE_QUEUES = tuple(role.queue for role in Role)
RETENTION_STATUSES = ('succeeded', 'failed', 'cancelled', 'aborted')

_CANDIDATES = """
SELECT job.id, job.worker_id, job.attempts
  FROM procrastinate_jobs job
 WHERE job.status = 'doing'
   AND job.queue_name = ANY(%(queues)s)
   AND NOT EXISTS (
       SELECT 1 FROM procrastinate_workers worker
        WHERE worker.id = job.worker_id
          AND worker.last_heartbeat >= now() - make_interval(secs => %(stalled)s)
   )
 ORDER BY job.id
 LIMIT %(batch)s
"""

_REVALIDATE = """
SELECT job.worker_id, job.attempts, job.status::text AS status,
       job.abort_requested,
       EXISTS (
           SELECT 1 FROM procrastinate_workers worker
            WHERE worker.id = job.worker_id
              AND worker.last_heartbeat >= now() - make_interval(secs => %(stalled)s)
       ) AS worker_alive
  FROM procrastinate_jobs job
 WHERE job.id = %(id)s
   FOR UPDATE
"""

_DELETE_OLD_JOBS = """
DELETE FROM procrastinate_jobs
 WHERE id IN (
     SELECT job.id
       FROM procrastinate_jobs job
      WHERE job.status = ANY(%(statuses)s::procrastinate_job_status[])
        AND NOT EXISTS (
            SELECT 1 FROM procrastinate_periodic_defers defer
             WHERE defer.job_id = job.id
        )
        AND (
            SELECT max(event.at) FROM procrastinate_events event
             WHERE event.job_id = job.id
        ) < now() - make_interval(hours => %(hours)s)
      ORDER BY job.id
      LIMIT %(batch)s
 )
"""


class Decision(str, enum.Enum):
    RETRIED = 'retried'
    FAILED = 'failed'
    DROPPED = 'dropped'


@dataclass(frozen=True)
class Candidate:
    id: int
    worker_id: int | None
    attempts: int


@dataclass(frozen=True)
class PassResult:
    ran: bool
    decisions: dict[int, Decision]
    deleted: int = 0


class RecoveryPass:
    """One recovery transaction. Split into steps so tests can interleave."""

    def __init__(
        self,
        conn: psycopg.Connection,
        *,
        stalled_seconds: float,
        batch: int,
        max_attempts: int = MAX_ATTEMPTS,
    ):
        self._conn = conn
        self._stalled = stalled_seconds
        self._batch = batch
        self._max_attempts = max_attempts

    def try_lock(self) -> bool:
        row = self._conn.execute(
            'SELECT pg_try_advisory_xact_lock(%s) AS locked', (RECOVERY_LOCK_KEY,)
        ).fetchone()
        return bool(row and row['locked'])

    def candidates(self) -> list[Candidate]:
        rows = self._conn.execute(
            _CANDIDATES,
            {
                'queues': list(RECOVERABLE_QUEUES),
                'stalled': self._stalled,
                'batch': self._batch,
            },
        ).fetchall()
        return [Candidate(r['id'], r['worker_id'], r['attempts']) for r in rows]

    def recover(self, candidate: Candidate) -> Decision:
        current = self._conn.execute(
            _REVALIDATE, {'id': candidate.id, 'stalled': self._stalled}
        ).fetchone()
        if (
            current is None
            or current['status'] != 'doing'
            or current['worker_alive']
            or current['worker_id'] != candidate.worker_id
            or current['attempts'] != candidate.attempts
        ):
            return Decision.DROPPED
        if current['abort_requested'] or candidate.attempts + 1 >= self._max_attempts:
            self._conn.execute(
                "SELECT procrastinate_finish_job_v1(%s, 'failed', false)",
                (candidate.id,),
            )
            return Decision.FAILED
        self._conn.execute(
            'SELECT procrastinate_retry_job_v2(%s, now(), NULL, NULL, NULL)',
            (candidate.id,),
        )
        return Decision.RETRIED

    def run(self) -> PassResult:
        if not self.try_lock():
            return PassResult(ran=False, decisions={})
        return PassResult(
            ran=True,
            decisions={c.id: self.recover(c) for c in self.candidates()},
        )


class RecoveryLoop:
    """Supervised recovery and retention on a dedicated thread and connection."""

    def __init__(
        self,
        conninfo: str,
        registry: ExecutionRegistry,
        *,
        interval: float,
        stalled_seconds: float,
        pass_budget: float,
        pass_grace: float,
        batch: int = 100,
        retention_hours: int = 24 * 7,
    ):
        self._conninfo = conninfo
        self._registry = registry
        self._interval = interval
        self._stalled = stalled_seconds
        self._pass_budget = pass_budget
        self._pass_grace = pass_grace
        self._batch = batch
        self._retention_hours = retention_hours
        self._conn: psycopg.Connection | None = None
        self._passes = itertools.count(1)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name='task-queue-recovery', daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _connect(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            timeout_ms = int(self._pass_budget * 1000)
            self._conn = psycopg.connect(
                self._conninfo,
                autocommit=True,
                row_factory=dict_row,
                options=f'-c statement_timeout={timeout_ms} '
                f'-c idle_in_transaction_session_timeout={timeout_ms}',
            )
        return self._conn

    def _close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def run_pass(self) -> PassResult:
        number = next(self._passes)
        token = self._registry.start(
            'recovery', f'pass-{number}', 0, self._pass_budget, self._pass_grace
        )
        try:
            conn = self._connect()
            with conn.transaction():
                result = RecoveryPass(
                    conn, stalled_seconds=self._stalled, batch=self._batch
                ).run()
            if result.ran:
                with conn.transaction():
                    deleted = conn.execute(
                        _DELETE_OLD_JOBS,
                        {
                            'statuses': list(RETENTION_STATUSES),
                            'hours': self._retention_hours,
                            'batch': self._batch,
                        },
                    ).rowcount
                result = PassResult(True, result.decisions, deleted)
        except Exception:
            self._close()
            raise
        finally:
            self._registry.finish(token)
        self._log(number, result)
        return result

    def _log(self, number: int, result: PassResult) -> None:
        if not (result.decisions or result.deleted):
            return
        extra: dict[str, Any] = {
            'pass': number,
            'decisions': {str(k): v.value for k, v in result.decisions.items()},
            'deleted': result.deleted,
        }
        logger.info('task_queue.recovery_pass', extra=extra)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_pass()
            except Exception:
                logger.exception('task_queue.recovery_pass_failed')
            self._stop.wait(self._interval)
        self._close()
