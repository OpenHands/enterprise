"""Test-only: run the real worker entrypoint with a misbehaving job body.

``HARNESS_BODY`` picks what every job does:

- ``freeze``: block the event loop thread (a frozen loop).
- ``swallow_cancel``: keep the loop responsive but ignore cancellation, so
  the in-loop timeout can never complete.
"""

import asyncio
import os
import time
from pathlib import Path

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


async def freeze_once() -> None:
    """Freeze on the first run; later runs record that they completed."""
    marker = Path(os.environ['HARNESS_MARKER'])
    if not marker.exists():
        marker.write_text('froze\n')
        time.sleep(3600)
    with marker.open('a') as out:
        out.write('completed\n')


BODIES = {
    'freeze': freeze,
    'freeze_once': freeze_once,
    'swallow_cancel': swallow_cancel,
}

if __name__ == '__main__':
    body = BODIES[os.environ['HARNESS_BODY']]
    ScheduledJob.load = lambda self: body  # type: ignore[method-assign]
    worker.main()
