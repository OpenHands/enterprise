"""APScheduler 3.x in every replica, in-memory job store, Postgres run-once guard."""

import logging
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from poc_job import INTERVAL, claim, slot_for, stub_job

# Off only in candidate_unguarded, the negative control.
GUARD = os.environ.get('POC_GUARD', '1').lower() in ('true', '1')

logging.basicConfig(level=logging.INFO)


async def tick() -> None:
    # 3.x doesn't hand the scheduled run time to the job; slot_for() rounds to it.
    slot = slot_for()
    if GUARD and not await claim('tick', slot):
        return
    await stub_job('tick', slot)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Default MemoryJobStore: 3.x must not share a persistent store across processes.
    scheduler = AsyncIOScheduler(timezone='UTC')
    scheduler.add_job(
        tick,
        # '0,10,...' not '*/N': CronTrigger rejects '*/60'. INTERVAL must divide 60.
        CronTrigger(second=','.join(map(str, range(0, 60, INTERVAL))), timezone='UTC'),
        id='tick',
        # Under half an interval, so a late run still rounds to its own slot.
        misfire_grace_time=max(1, INTERVAL // 2 - 1),
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)
