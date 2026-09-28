"""Celery app: Beat publishes `tick` every POC_INTERVAL_SECONDS; workers run it."""

import os
from datetime import timedelta

from celery import Celery
from poc_job import INTERVAL, run_stub, slot_for

app = Celery('poc', broker=os.environ.get('POC_BROKER_URL', 'redis://redis:6379/0'))
app.conf.update(
    # At-least-once: ack after the task returns, requeue if the pool child dies.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Documented pairing with acks_late: don't reserve extra messages per process.
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        'tick': {'task': 'tasks.tick', 'schedule': timedelta(seconds=INTERVAL)}
    },
)


@app.task
def tick() -> None:
    # Beat sends no scheduled time (no ETA, no header), so round the run time.
    run_stub('tick', slot_for())
