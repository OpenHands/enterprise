"""The worker takes jobs only from the queues its own tasks use."""

import logging

import pytest

from openhands.app_server.utils.logger import openhands_logger
from openhands.app_server.worker import __main__ as worker_main
from openhands.app_server.worker.app import QUEUES, app

BLUEPRINT_NAMESPACES = ('worker:', 'sandbox_lifecycle:')


def test_every_registered_task_uses_a_queue_the_worker_takes_jobs_from():
    tasks = [
        task
        for name, task in app.tasks.items()
        if name.startswith(BLUEPRINT_NAMESPACES)
    ]

    assert tasks
    assert {task.queue for task in tasks} <= set(QUEUES)


@pytest.mark.usefixtures('app_db_session')
async def test_worker_runs_on_its_queues_and_logs_them(monkeypatch, caplog):
    started_with: dict = {}

    async def run_worker_async(**kwargs):
        # The real call runs until the process is stopped.
        started_with.update(kwargs)

    monkeypatch.setattr(app, 'run_worker_async', run_worker_async)
    monkeypatch.setattr(openhands_logger, 'propagate', True)

    with caplog.at_level(logging.INFO, logger=openhands_logger.name):
        await worker_main.run()

    assert started_with['queues'] == QUEUES == ['default']
    [started] = [r for r in caplog.records if r.getMessage() == 'worker.started']
    assert started.queues == QUEUES
