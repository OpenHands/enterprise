"""Task queue worker: ``python -m server.task_queue.worker``.

``TASK_QUEUE_ROLE`` (``business`` or ``ops``) picks the queue consumed and the
periodic jobs scheduled. ``TASK_QUEUE_SCHEDULING_ENABLED`` separately switches
scheduling off without changing what the worker consumes, so a rollback can stop
new occurrences while the worker drains the ones already queued.

The process exits whenever the Procrastinate worker stops. The worker stops
itself on database loss but would otherwise leave a healthy-looking container
behind, so any stop that was not requested by a signal exits non-zero and the
platform restarts it.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from procrastinate.worker import Worker

from openhands.app_server.utils.logger import openhands_logger as logger
from server.task_queue.app import build_app
from server.task_queue.config import Settings


def _route_procrastinate_logs() -> None:
    procrastinate_logger = logging.getLogger('procrastinate')
    procrastinate_logger.handlers = list(logger.handlers)
    procrastinate_logger.setLevel(logging.INFO)
    procrastinate_logger.propagate = False


def build_worker(settings: Settings, app) -> Worker:
    return Worker(
        app=app,
        queues=[settings.role.queue],
        name=f'task-queue-{settings.role.value}',
        concurrency=settings.concurrency,
        fetch_job_polling_interval=settings.fetch_job_polling_interval,
        listen_notify=settings.listen_notify,
        update_heartbeat_interval=settings.update_heartbeat_interval,
        stalled_worker_timeout=settings.stalled_worker_timeout,
        # Unset on purpose: a graceful timeout marks unfinished jobs aborted,
        # and stalled recovery never picks aborted jobs up again.
        shutdown_graceful_timeout=None,
        install_signal_handlers=False,
    )


async def run(settings: Settings) -> int:
    """Run until stopped; return the process exit code."""
    app = build_app(settings)
    requested = asyncio.Event()

    async with app.open_async():
        worker = build_worker(settings, app)

        def on_signal(signum: int) -> None:
            logger.info('task_queue.stop_requested', extra={'signal': signum})
            requested.set()
            worker.stop()

        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, on_signal, signum)
        try:
            logger.info(
                'task_queue.worker_starting',
                extra={
                    'role': settings.role.value,
                    'scheduling_enabled': settings.scheduling_enabled,
                    'scheduled': sorted(
                        name for name, _ in app.periodic_registry.periodic_tasks
                    ),
                },
            )
            await worker.run()
        except Exception:
            logger.exception('task_queue.worker_crashed')
            return 1
        finally:
            for signum in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(signum)

    if requested.is_set():
        logger.info('task_queue.worker_stopped')
        return 0
    logger.error('task_queue.worker_stopped_unrequested')
    return 1


def main() -> None:
    _route_procrastinate_logs()
    sys.exit(asyncio.run(run(Settings.from_env())))


if __name__ == '__main__':
    main()
