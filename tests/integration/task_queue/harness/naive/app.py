"""Uncoordinated in-process scheduler: the harness's negative control."""

import asyncio
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from poc_job import INTERVAL, claim, slot_for, stub_job

GUARD = os.environ.get('POC_GUARD', '0').lower() in ('true', '1')
# P7 positive control: on shutdown, wait for running jobs instead of dropping them.
DRAIN = os.environ.get('POC_DRAIN', '0').lower() in ('true', '1')
running: set[asyncio.Task] = set()


async def schedule() -> None:
    while True:
        await asyncio.sleep(INTERVAL - time.time() % INTERVAL)
        slot = slot_for()
        if GUARD and not await claim('tick', slot):
            continue
        job = asyncio.create_task(stub_job('tick', slot))
        running.add(job)
        job.add_done_callback(running.discard)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(schedule())
    yield
    task.cancel()
    if DRAIN and running:
        await asyncio.gather(*running)


app = FastAPI(lifespan=lifespan)
