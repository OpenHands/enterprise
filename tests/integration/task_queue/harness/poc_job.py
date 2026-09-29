"""Stub job and run-once guard shared by every candidate.

Each run writes one row to ``poc_job_runs``; the tests read those rows to
decide whether an occurrence ran once, twice, or not at all.
"""

import asyncio
import os
import socket
import sys
import time
from datetime import UTC, datetime

import psycopg

DSN = os.environ.get('POC_DATABASE_URL', 'postgresql://poc_app:poc_app@postgres/poc')
INTERVAL = int(os.environ.get('POC_INTERVAL_SECONDS', '10'))
JOB_SECONDS = float(os.environ.get('POC_JOB_SECONDS', '1'))
REPLICA = socket.gethostname()
# P9: how the job does its work. '0' awaits (non-blocking async); '1' blocks the
# event loop, as sync code in an async job does today; 'thread' runs the same
# blocking work via asyncio.to_thread, the async-safe way to call sync code.
BLOCKING = os.environ.get('POC_JOB_BLOCKING', '0').lower()


def slot_for(t: datetime | None = None) -> datetime:
    """The occurrence a firing belongs to, for frameworks that don't pass one.

    Rounds to the nearest interval, so a replica whose clock is off by less
    than half an interval still maps to the same occurrence.
    """
    ts = (t or datetime.now(UTC)).timestamp()
    return datetime.fromtimestamp(round(ts / INTERVAL) * INTERVAL, UTC)


async def claim(job: str, slot: datetime) -> bool:
    """Run-once guard: True for exactly one caller per (job, slot)."""
    async with await psycopg.AsyncConnection.connect(DSN, autocommit=True) as conn:
        cur = await conn.execute(
            'INSERT INTO poc_job_claims (job, slot, replica) VALUES (%s, %s, %s) '
            'ON CONFLICT DO NOTHING RETURNING 1',
            (job, slot, REPLICA),
        )
        return await cur.fetchone() is not None


async def start_run(job: str, slot: datetime) -> int:
    async with await psycopg.AsyncConnection.connect(DSN, autocommit=True) as conn:
        cur = await conn.execute(
            'INSERT INTO poc_job_runs (job, slot, replica, fired_at) '
            'VALUES (%s, %s, %s, %s) RETURNING id',
            (job, slot, REPLICA, datetime.now(UTC)),
        )
        row = await cur.fetchone()
        assert row is not None
        return row[0]


async def finish_run(run_id: int, seconds: float | None = None) -> None:
    """Do the 'work', then mark the run finished. Safe to repeat."""
    duration = JOB_SECONDS if seconds is None else seconds
    if BLOCKING in ('true', '1'):
        time.sleep(duration)  # noqa: ASYNC251 - deliberately blocks the event loop
    elif BLOCKING == 'thread':
        await asyncio.to_thread(time.sleep, duration)
    else:
        await asyncio.sleep(duration)
    async with await psycopg.AsyncConnection.connect(DSN, autocommit=True) as conn:
        await conn.execute(
            'UPDATE poc_job_runs SET finished_at = now(), finished_by = %s '
            'WHERE id = %s AND finished_at IS NULL',
            (REPLICA, run_id),
        )


async def stub_job(job: str = 'tick', slot: datetime | None = None) -> None:
    await finish_run(await start_run(job, slot or slot_for()))


def run_stub(job: str = 'tick', slot: datetime | None = None) -> None:
    """Synchronous entrypoint, for workers without asyncio."""
    asyncio.run(stub_job(job, slot))


if __name__ == '__main__':
    # `python -m poc_job [job]`, for candidates that run a command per occurrence.
    run_stub(sys.argv[1] if len(sys.argv) > 1 else 'tick')
