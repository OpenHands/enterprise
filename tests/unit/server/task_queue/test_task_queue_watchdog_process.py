"""The watchdog end to end: a real worker process exits itself.

No probes and no orchestrator are involved, which is the Docker Compose case:
the process has to leave on its own for ``restart: unless-stopped`` to act.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import pytest

from server.task_queue.app import build_app
from server.task_queue.config import Role, Settings
from server.task_queue.jobs import JOBS_BY_NAME
from server.task_queue.watchdog import WATCHDOG_EXIT_CODE
from tests import postgres_testdb

HARNESS = Path(__file__).with_name('worker_harness.py')
REPO_ROOT = Path(__file__).resolve().parents[4]
ENRICH = JOBS_BY_NAME['enrich_user_interaction_data']


async def _row(conninfo: str, job_id: int) -> tuple:
    async with await psycopg.AsyncConnection.connect(conninfo) as conn:
        cursor = await conn.execute(
            'SELECT status::text, abort_requested, worker_id '
            'FROM procrastinate_jobs WHERE id = %s',
            (job_id,),
        )
        return await cursor.fetchone()


async def _defer(conninfo: str) -> int:
    app = build_app(Settings(role=Role.BUSINESS, conninfo=conninfo))
    async with app.open_async():
        return await app.tasks[ENRICH.task_name].defer_async(timestamp=0)


async def _wait_doing(conninfo: str, job_id: int, process: subprocess.Popen) -> float:
    for _ in range(300):
        if (await _row(conninfo, job_id))[0] == 'doing':
            return time.monotonic()
        assert process.poll() is None, 'worker exited before starting the job'
        await asyncio.sleep(0.1)
    raise AssertionError('job never started')


@pytest.fixture
def spawn(test_database: postgres_testdb.TestDatabase):
    server = test_database.server
    processes: list[subprocess.Popen] = []

    def _spawn(body: str, role: str = 'business', **env: str) -> subprocess.Popen:
        full_env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(('DB_', 'GCP_', 'TASK_QUEUE_'))
        }
        full_env.update(
            PYTHONPATH=str(REPO_ROOT),
            HARNESS_BODY=body,
            DB_HOST=server.host,
            DB_PORT=str(server.port),
            DB_NAME=test_database.name,
            DB_USER=server.user,
            DB_PASS=server.password,
            TASK_QUEUE_ROLE=role,
            TASK_QUEUE_PROBE_PORT='0',
            TASK_QUEUE_WATCHDOG_INTERVAL_SECONDS='1',
            **env,
        )
        process = subprocess.Popen(
            [sys.executable, str(HARNESS)],
            cwd=REPO_ROOT,
            env=full_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        processes.append(process)
        return process

    yield _spawn
    for process in processes:
        process.kill()
        process.wait()


async def _exit_code(process: subprocess.Popen, timeout: float) -> int:
    return await asyncio.to_thread(process.wait, timeout)


async def test_frozen_event_loop_exits_and_leaves_the_job_recoverable(spawn, conninfo):
    job_id = await _defer(conninfo)
    bystander = spawn('freeze', role='ops')
    frozen = spawn('freeze', TASK_QUEUE_LOOP_STALL_TIMEOUT_SECONDS='3')
    started = await _wait_doing(conninfo, job_id, frozen)

    assert await _exit_code(frozen, 30) == WATCHDOG_EXIT_CODE
    latency = time.monotonic() - started
    # stall threshold + one check interval + bounded exit report, with slack
    assert latency < 3 + 1 + 1 + 5, latency
    status, abort_requested, _ = await _row(conninfo, job_id)
    assert (status, abort_requested) == ('doing', False)
    assert bystander.poll() is None, 'only the offending process may exit'


async def test_task_ignoring_cancellation_is_killed_after_grace(spawn, conninfo):
    job_id = await _defer(conninfo)
    stuck = spawn(
        'swallow_cancel',
        TASK_QUEUE_ENRICH_USER_INTERACTION_DATA_BUDGET_SECONDS='1',
        TASK_QUEUE_WATCHDOG_GRACE_SECONDS='2',
    )
    started = await _wait_doing(conninfo, job_id, stuck)

    assert await _exit_code(stuck, 30) == WATCHDOG_EXIT_CODE
    assert time.monotonic() - started < 1 + 2 + 1 + 1 + 5
    status, abort_requested, _ = await _row(conninfo, job_id)
    assert (status, abort_requested) == ('doing', False)


async def test_frozen_job_is_recovered_by_ops_and_completed_by_a_new_worker(
    spawn, conninfo, tmp_path
):
    """The whole path: self-exit, stalled recovery, re-execution."""
    marker = tmp_path / 'marker'
    fast = {
        'HARNESS_MARKER': str(marker),
        'TASK_QUEUE_HEARTBEAT_INTERVAL_SECONDS': '1',
        'TASK_QUEUE_STALLED_WORKER_TIMEOUT_SECONDS': '3',
    }
    job_id = await _defer(conninfo)
    frozen = spawn('freeze_once', TASK_QUEUE_LOOP_STALL_TIMEOUT_SECONDS='3', **fast)
    await _wait_doing(conninfo, job_id, frozen)
    assert await _exit_code(frozen, 30) == WATCHDOG_EXIT_CODE

    spawn('freeze_once', role='ops', TASK_QUEUE_RECOVERY_INTERVAL_SECONDS='1', **fast)
    spawn('freeze_once', **fast)
    for _ in range(300):
        status = (await _row(conninfo, job_id))[0]
        if status == 'succeeded':
            break
        await asyncio.sleep(0.1)
    assert status == 'succeeded'
    assert marker.read_text() == 'froze\ncompleted\n'


async def test_original_process_exits_on_its_deadline_after_reassignment(
    spawn, conninfo
):
    """A replacement fetch overwrites worker_id; enforcement must not care."""
    job_id = await _defer(conninfo)
    frozen = spawn(
        'freeze',
        TASK_QUEUE_LOOP_STALL_TIMEOUT_SECONDS='300',
        TASK_QUEUE_ENRICH_USER_INTERACTION_DATA_BUDGET_SECONDS='3',
        TASK_QUEUE_WATCHDOG_GRACE_SECONDS='2',
    )
    await _wait_doing(conninfo, job_id, frozen)
    _, _, original_worker = await _row(conninfo, job_id)

    async with await psycopg.AsyncConnection.connect(conninfo) as conn:
        replacement = await (
            await conn.execute(
                'INSERT INTO procrastinate_workers DEFAULT VALUES RETURNING id'
            )
        ).fetchone()
        await conn.execute(
            'UPDATE procrastinate_jobs SET worker_id = %s, attempts = attempts + 1 '
            'WHERE id = %s',
            (replacement[0], job_id),
        )
        await conn.commit()
    assert (await _row(conninfo, job_id))[2] != original_worker

    assert await _exit_code(frozen, 30) == WATCHDOG_EXIT_CODE
