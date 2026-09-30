"""Test-only: run the real worker entrypoint with a misbehaving job body.

``HARNESS_BODY`` picks what every job does:

- ``freeze``: block the event loop thread (a frozen loop).
- ``swallow_cancel``: keep the loop responsive but ignore cancellation, so
  the in-loop timeout can never complete.
"""

import asyncio
import os
import time

from server.task_queue import worker
from server.task_queue.jobs import ScheduledJob


async def freeze() -> None:
    time.sleep(3600)


async def swallow_cancel() -> None:
    while True:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            continue


BODIES = {'freeze': freeze, 'swallow_cancel': swallow_cancel}

if __name__ == '__main__':
    body = BODIES[os.environ['HARNESS_BODY']]
    ScheduledJob.load = lambda self: body  # type: ignore[method-assign]
    worker.main()
