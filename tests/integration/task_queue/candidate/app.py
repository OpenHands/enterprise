"""FastAPI replica. With POC_SCHEDULE=1 (setup 3b) it also runs a guarded scheduler."""

import asyncio
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from poc_job import claim, slot_for
from tasks import TRIGGER, tick

SCHEDULE = os.environ.get('POC_SCHEDULE', '0').lower() in ('true', '1')


async def fire() -> None:
    # APScheduler 3.x does not pass the scheduled run time to the job.
    slot = slot_for()
    if await claim('tick', slot):
        await asyncio.to_thread(tick.send, slot.isoformat())


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler(timezone='UTC')
    if SCHEDULE:
        scheduler.add_job(fire, TRIGGER)
        scheduler.start()
    yield
    if SCHEDULE:
        scheduler.shutdown()


app = FastAPI(lifespan=lifespan)
