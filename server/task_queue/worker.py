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
import contextlib
import logging
import signal
import sys

from procrastinate import App
from procrastinate.worker import Worker

from openhands.app_server.utils.logger import openhands_logger as logger
from server.task_queue.app import build_app
from server.task_queue.config import Settings
from server.task_queue.watchdog import (
    ExecutionRegistry,
    LoopHeartbeat,
    ProbeServer,
    QueueRowLookup,
    Telemetry,
    Watchdog,
    execution_middleware,
)

LOOP_HEARTBEAT_INTERVAL = 1.0


def _route_procrastinate_logs() -> None:
    procrastinate_logger = logging.getLogger('procrastinate')
    procrastinate_logger.handlers = list(logger.handlers)
    procrastinate_logger.setLevel(logging.INFO)
    procrastinate_logger.propagate = False


def build_worker(
    settings: Settings, app: App, worker_middleware: list | None = None
) -> Worker:
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
        worker_middleware=worker_middleware,
    )


def _start_watchdog(
    settings: Settings, registry: ExecutionRegistry, stack: contextlib.ExitStack
) -> LoopHeartbeat:
    heartbeat = LoopHeartbeat()
    watchdog = Watchdog(
        registry,
        heartbeat,
        Telemetry(QueueRowLookup(settings.conninfo)),
        check_interval=settings.watchdog_interval,
        loop_stall_timeout=settings.loop_stall_timeout,
    )
    watchdog.start()
    stack.callback(watchdog.stop)
    if settings.probe_port:
        probe = ProbeServer(settings.probe_port, watchdog)
        probe.start()
        stack.callback(probe.stop)
    return heartbeat


async def run(settings: Settings) -> int:
    """Run until stopped; return the process exit code."""
    app = build_app(settings)
    registry = ExecutionRegistry()

    with contextlib.ExitStack() as threads:
        heartbeat = _start_watchdog(settings, registry, threads)
        heartbeat_task = asyncio.create_task(
            heartbeat.run(LOOP_HEARTBEAT_INTERVAL), name='loop-heartbeat'
        )
        try:
            async with app.open_async():
                middleware = execution_middleware(
                    registry, settings.budget_for, settings.watchdog_grace
                )
                worker = build_worker(settings, app, [middleware])
                return await _run_worker(settings, app, worker)
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task


async def _run_worker(settings: Settings, app: App, worker: Worker) -> int:
    requested = asyncio.Event()

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
