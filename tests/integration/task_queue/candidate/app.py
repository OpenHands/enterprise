"""Procrastinate worker inside a FastAPI app, with Postgres as the only broker."""

import asyncio
import contextlib
import os
from datetime import UTC, datetime

import procrastinate
from fastapi import FastAPI
from poc_job import DSN, INTERVAL, run_stub, stub_job

# Worker defaults: heartbeat every 10 s, a worker is stalled after 30 s without one.
# The retry task uses the same 30 s, i.e. three missed heartbeats, so a slow but
# live worker is not mistaken for a dead one.
HEARTBEAT_SECONDS = 10
# P9 variant: raising this above the longest blocking stretch avoids false stalls,
# but also delays detecting a worker that really died.
STALLED_SECONDS = int(os.environ.get('POC_STALLED_SECONDS', '30'))
STALLED_RETRY = os.environ.get('POC_STALLED_RETRY', '1').lower() in ('true', '1')

app = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=DSN))

# The optional 6th cron column is seconds; */60 is out of range, so a minute is plain cron.
CRON = '* * * * *' if INTERVAL == 60 else f'* * * * * */{INTERVAL}'


# P9 variant: a sync task runs in Procrastinate's worker thread, so blocking work
# inside it cannot starve the event loop that sends heartbeats.
SYNC_TASK = os.environ.get('POC_SYNC_TASK', '0').lower() in ('true', '1')
# P10 variant: the worker stops itself when it loses the database (its LISTEN
# connection fails). Exit the process so the platform restarts it, as the
# `procrastinate worker` CLI does; otherwise the app stays up with no worker.
SUPERVISE = os.environ.get('POC_SUPERVISE', '0').lower() in ('true', '1')

if SYNC_TASK:

    @app.periodic(cron=CRON)
    @app.task(name='tick')
    def tick(timestamp: int) -> None:
        run_stub('tick', datetime.fromtimestamp(timestamp, UTC))

else:

    @app.periodic(cron=CRON)
    @app.task(name='tick')
    async def tick(timestamp: int) -> None:
        await stub_job('tick', datetime.fromtimestamp(timestamp, UTC))


if STALLED_RETRY:
    # The documented recipe, run every minute instead of every 10 minutes so a dead
    # replica's job is back in the queue within about 90 s.
    @app.periodic(cron='* * * * *')
    @app.task(queueing_lock='retry_stalled_jobs', pass_context=True)
    async def retry_stalled_jobs(context, timestamp: int) -> None:
        for job in await app.job_manager.get_stalled_jobs(
            seconds_since_heartbeat=STALLED_SECONDS
        ):
            await app.job_manager.retry_job(job)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    async with app.open_async():
        worker = asyncio.create_task(
            app.run_worker_async(
                install_signal_handlers=False,
                concurrency=4,  # a 30 s tick must not block the retry task
                update_heartbeat_interval=HEARTBEAT_SECONDS,
                stalled_worker_timeout=STALLED_SECONDS,
            )
        )
        if SUPERVISE:

            def exit_if_stopped(task: asyncio.Task) -> None:
                if not task.cancelled():
                    print(
                        'procrastinate worker stopped; exiting for a restart',
                        flush=True,
                    )
                    os._exit(1)

            worker.add_done_callback(exit_if_stopped)
        yield
        # Cancel = graceful stop: no new jobs, wait for running ones
        # (shutdown_graceful_timeout defaults to None). No timeout here, unlike the
        # docs example's wait_for(10), which closes the pool under running jobs. At
        # the deploy's SIGKILL the job stays `doing` and the stalled-job retry
        # re-queues it; a finite graceful timeout would mark it `aborted` instead.
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker


api = FastAPI(lifespan=lifespan)
