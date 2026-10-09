"""Run the background worker.

    python -m openhands.app_server.worker

The worker needs the app server's environment, and the database migrated to
head. It exits when it cannot reach the database.
"""

import asyncio

from dotenv import load_dotenv

load_dotenv()

from openhands.agent_server.env_parser import from_env  # noqa: E402
from openhands.app_server.config import get_global_config  # noqa: E402
from openhands.app_server.utils.logger import openhands_logger  # noqa: E402
from openhands.app_server.worker.app import QUEUES, WorkerConfig, app  # noqa: E402
from openhands.app_server.worker.database import build_connector  # noqa: E402


async def run() -> None:
    config: WorkerConfig = from_env(WorkerConfig, 'OH_WORKER')
    connector = build_connector(
        get_global_config().db_session,
        # One connection per running job, plus the worker's own queries.
        pool_size=config.concurrency + 2,
    )
    with app.replace_connector(connector):
        async with app.open_async():
            openhands_logger.info(
                'worker.started',
                extra={'concurrency': config.concurrency, 'queues': QUEUES},
            )
            await app.run_worker_async(concurrency=config.concurrency, queues=QUEUES)


def main() -> None:
    asyncio.run(run())


if __name__ == '__main__':
    main()
