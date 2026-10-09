"""Run the background worker.

    python -m openhands.app_server.worker

The worker needs the app server's environment, and the database migrated to
head. It exits when it cannot reach the database.

The first SIGTERM or SIGINT stops it taking jobs and waits for running ones to
finish. A second one stops every running job's child process, then the worker.
"""

import asyncio
import os
import signal

from dotenv import load_dotenv

load_dotenv()

from openhands.agent_server.env_parser import from_env  # noqa: E402
from openhands.app_server.config import get_global_config  # noqa: E402
from openhands.app_server.utils.logger import openhands_logger  # noqa: E402
from openhands.app_server.worker.app import QUEUES, WorkerConfig, app  # noqa: E402
from openhands.app_server.worker.child_job import stop_all_children  # noqa: E402
from openhands.app_server.worker.database import build_connector  # noqa: E402
from openhands.app_server.worker.housekeeping import SCHEDULED_JOBS_QUEUE  # noqa: E402
from openhands.app_server.worker.scheduled_jobs import (  # noqa: E402
    configure,
    register_scheduled_jobs,
    scheduled_jobs_enabled,
)


def install_forced_stop() -> None:
    """Make a second SIGTERM or SIGINT stop every running child, then the worker.

    procrastinate handles the first signal itself, then puts back the handler
    that was installed before it started. Without one, the second signal killed
    the worker at once and left each running job's child process behind; with
    this one, every job ends as failed once its child is gone, and the worker's
    own shutdown completes.
    """
    loop = asyncio.get_running_loop()
    pending: set[asyncio.Task] = set()

    def stop_children(signum: int) -> None:
        openhands_logger.warning(
            'worker.forced_stop', extra={'signal': signal.Signals(signum).name}
        )
        task = loop.create_task(stop_all_children())
        pending.add(task)
        task.add_done_callback(pending.discard)

    def handler(signum: int, frame: object) -> None:
        loop.call_soon_threadsafe(stop_children, signum)

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


async def run() -> None:
    config: WorkerConfig = from_env(WorkerConfig, 'OH_WORKER')
    # Read once, before connecting: a bad value stops the worker at startup.
    scheduling = scheduled_jobs_enabled(os.environ)
    queues = list(QUEUES)
    scheduled = configure(os.environ) if scheduling else []
    if scheduling:
        register_scheduled_jobs(app, scheduled)
        queues.append(SCHEDULED_JOBS_QUEUE)
    connector = build_connector(
        get_global_config().db_session,
        # One connection per running job, plus the worker's own queries.
        pool_size=config.concurrency + 2,
    )
    with app.replace_connector(connector):
        async with app.open_async():
            openhands_logger.info(
                'worker.started',
                extra={
                    'concurrency': config.concurrency,
                    'queues': queues,
                    'scheduled_jobs_enabled': scheduling,
                    'scheduled_jobs': [item.job.name for item in scheduled],
                },
            )
            install_forced_stop()
            await app.run_worker_async(concurrency=config.concurrency, queues=queues)


def main() -> None:
    asyncio.run(run())


if __name__ == '__main__':
    main()
