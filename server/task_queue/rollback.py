"""Rollback to the CronJobs: retire queued work so nothing replays later.

Run only after every worker and recovery loop is stopped (T1.10.2 step 3).
``procrastinate_retry_job_v2`` accepts ``doing`` and ``failed`` jobs, so a
sweep while anything can still retry - a running job, or a recovery pass - can
re-queue work the sweep already cleared. ``reconcile`` therefore refuses while
any worker's heartbeat is fresh.

::

    python -m server.task_queue.rollback reconcile   # step 4
    python -m server.task_queue.rollback verify      # step 5: the gate

``reconcile`` cancels every ``todo`` job of the scheduled tasks and fails every
residual ``doing`` one, so no execution lock stays held. ``verify`` exits
non-zero unless no ``todo`` or ``doing`` row is left. The schema stays in place:
without a worker it is inert.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

import psycopg
from psycopg.rows import dict_row

from server.task_queue.config import conninfo_from_env
from server.task_queue.jobs import JOBS

TASK_NAMES = tuple(job.task_name for job in JOBS)
DEFAULT_LIVE_WINDOW_SECONDS = 30.0


class WorkersStillRunning(RuntimeError):
    pass


@dataclass(frozen=True)
class Reconciled:
    cancelled: int
    failed: int


def live_workers(conn: psycopg.Connection, window_seconds: float) -> list[int]:
    rows = conn.execute(
        'SELECT id FROM procrastinate_workers '
        'WHERE last_heartbeat >= now() - make_interval(secs => %s) ORDER BY id',
        (window_seconds,),
    ).fetchall()
    return [row['id'] for row in rows]


def remaining(conninfo: str, task_names: tuple[str, ...] = TASK_NAMES) -> dict:
    with psycopg.connect(conninfo, row_factory=dict_row) as conn:
        rows = conn.execute(
            'SELECT status::text AS status, count(*) AS n FROM procrastinate_jobs '
            "WHERE task_name = ANY(%s) AND status IN ('todo', 'doing') "
            'GROUP BY status',
            (list(task_names),),
        ).fetchall()
    counts = {'todo': 0, 'doing': 0}
    counts.update({row['status']: row['n'] for row in rows})
    return counts


def reconcile(
    conninfo: str,
    task_names: tuple[str, ...] = TASK_NAMES,
    *,
    live_window_seconds: float = DEFAULT_LIVE_WINDOW_SECONDS,
) -> Reconciled:
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as conn:
        alive = live_workers(conn, live_window_seconds)
        if alive:
            raise WorkersStillRunning(
                f'workers {alive} heartbeat within {live_window_seconds}s; stop '
                'every business and ops worker before reconciling'
            )
        with conn.transaction():
            jobs = conn.execute(
                'SELECT id, status::text AS status FROM procrastinate_jobs '
                "WHERE task_name = ANY(%s) AND status IN ('todo', 'doing') "
                'ORDER BY id FOR UPDATE',
                (list(task_names),),
            ).fetchall()
            cancelled = failed = 0
            for job in jobs:
                if job['status'] == 'todo':
                    conn.execute(
                        'SELECT procrastinate_cancel_job_v1(%s, false, false)',
                        (job['id'],),
                    )
                    cancelled += 1
                else:
                    conn.execute(
                        "SELECT procrastinate_finish_job_v1(%s, 'failed', false)",
                        (job['id'],),
                    )
                    failed += 1
    return Reconciled(cancelled=cancelled, failed=failed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m server.task_queue.rollback')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('reconcile', help='retire todo and doing scheduled jobs')
    commands.add_parser('verify', help='exit 1 unless no todo or doing job remains')
    args = parser.parse_args(argv)

    conninfo = conninfo_from_env()
    if args.command == 'reconcile':
        try:
            result = reconcile(conninfo)
        except WorkersStillRunning as error:
            print(f'refusing to reconcile: {error}', file=sys.stderr)
            return 1
        print(f'cancelled={result.cancelled} failed={result.failed}')
        return 0
    counts = remaining(conninfo)
    print(f'todo={counts["todo"]} doing={counts["doing"]}')
    return 0 if counts == {'todo': 0, 'doing': 0} else 1


if __name__ == '__main__':
    sys.exit(main())
