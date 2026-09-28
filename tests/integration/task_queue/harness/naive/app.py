"""Uncoordinated in-process scheduler: the harness's negative control."""

import asyncio
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from poc_job import INTERVAL, claim, slot_for, stub_job

GUARD = os.environ.get('POC_GUARD', '0').lower() in ('true', '1')


async def schedule() -> None:
    while True:
        await asyncio.sleep(INTERVAL - time.time() % INTERVAL)
        slot = slot_for()
        if GUARD and not await claim('tick', slot):
            continue
        asyncio.create_task(stub_job('tick', slot))


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(schedule())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)
