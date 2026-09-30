"""Run the background worker.

    python -m openhands.app_server.worker
    python -m openhands.app_server.worker healthcheck

The worker needs the app server's environment, and the database migrated to
head. ``healthcheck`` exits non-zero when the worker cannot reach the database
or procrastinate's tables are missing.
"""

import argparse
import asyncio

from dotenv import load_dotenv

load_dotenv()

from openhands.agent_server.env_parser import from_env  # noqa: E402
from openhands.app_server.config import get_global_config  # noqa: E402
from openhands.app_server.utils.logger import openhands_logger  # noqa: E402
from openhands.app_server.worker.app import WorkerConfig, app  # noqa: E402
from openhands.app_server.worker.database import build_connector  # noqa: E402


async def run(healthcheck: bool) -> None:
    config: WorkerConfig = from_env(WorkerConfig, 'OH_WORKER')
    connector = build_connector(
        get_global_config().db_session,
        # One connection per running job, plus the worker's own queries.
        pool_size=config.concurrency + 2,
    )
    with app.replace_connector(connector):
        async with app.open_async():
            if healthcheck:
                if not await app.check_connection_async():
                    raise SystemExit(
                        'The procrastinate_jobs table is missing. Migrate '
                        'the database to head.'
                    )
                return
            openhands_logger.info(
                'worker.started', extra={'concurrency': config.concurrency}
            )
            await app.run_worker_async(concurrency=config.concurrency)


def main() -> None:
    parser = argparse.ArgumentParser(prog='python -m openhands.app_server.worker')
    parser.add_argument('command', nargs='?', choices=['run', 'healthcheck'])
    args = parser.parse_args()
    asyncio.run(run(healthcheck=args.command == 'healthcheck'))


if __name__ == '__main__':
    main()
