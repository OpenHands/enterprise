"""Dramatiq broker and the one actor. Imported by workers, replicas and the scheduler."""

import os
from datetime import datetime

import dramatiq
from apscheduler.triggers.cron import CronTrigger
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import AsyncIO
from poc_job import INTERVAL, stub_job

broker = RedisBroker(url=os.environ.get('POC_REDIS_URL', 'redis://redis:6379/0'))
broker.add_middleware(AsyncIO())  # required for async def actors
dramatiq.set_broker(broker)

# Wall-clock aligned, so every scheduler fires the same occurrence. A cron step
# can't exceed its field's range, so whole minutes go in the minute field.
# ponytail: assumes INTERVAL divides 60 s or 60 min (the tests use 10 and 60).
TRIGGER = (
    CronTrigger(second=f'*/{INTERVAL}', timezone='UTC')
    if INTERVAL < 60
    else CronTrigger(minute=f'*/{INTERVAL // 60}', second=0, timezone='UTC')
)


@dramatiq.actor
async def tick(slot: str) -> None:
    await stub_job('tick', datetime.fromisoformat(slot))
