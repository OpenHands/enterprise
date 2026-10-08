"""Run a job's module in a child process, the way a Kubernetes Job runs its pod.

The worker's event loop never runs the job's code; a child process does. A job
that blocks therefore cannot stop the worker's heartbeats, and the job's
existing ``python -m <module>`` entry point runs unchanged.

Like a Job, a failed attempt is retried after a backoff, and one deadline covers
every attempt, the backoffs between them and the checks before them. A child
that outlives the deadline, or the worker's cancellation of the job, is stopped
with its whole process group before this returns: a worker slot is never freed
while the job's process still runs. ``stop_all_children`` does the same for every
running child at once when the worker is stopped by force.
"""

import asyncio
import logging
import os
import signal
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

_logger = logging.getLogger(__name__)

# A Job's pod backoff: 10 s, doubling, at most 6 min.
BACKOFF_BASE_SECONDS = 10.0
BACKOFF_MAX_SECONDS = 360.0
# How long a child has to exit after SIGTERM before it is killed.
STOP_GRACE_SECONDS = 30.0
# How long to wait before reading the job's status again after a failed read.
STATUS_RETRY_SECONDS = 5.0

# Every running child, so a forced stop of the worker can stop them all.
_live: set[asyncio.subprocess.Process] = set()
_stop_requested = asyncio.Event()


class ChildJobError(Exception):
    def __init__(self, module: str, attempts: int, exit_code: int | None):
        super().__init__(module, attempts, exit_code)
        self.module = module
        self.attempts = attempts
        self.exit_code = exit_code


class ChildJobFailed(ChildJobError):
    """Every attempt exited with a non-zero code."""


class ChildJobDeadlineExceeded(ChildJobError):
    """The deadline passed. A child still running was stopped, and not retried."""


class ChildJobStopped(ChildJobError):
    """The worker was stopped by force. A running child was stopped; no attempt follows."""


@dataclass(frozen=True)
class ChildJobResult:
    attempts: int
    # The job was no longer ours before an attempt, so that attempt never started.
    superseded: bool


async def run_child_job(
    module: str,
    *,
    env: Mapping[str, str],
    deadline_seconds: float,
    backoff_limit: int,
    still_current: Callable[[], Awaitable[bool]],
    backoff_base_seconds: float = BACKOFF_BASE_SECONDS,
    backoff_max_seconds: float = BACKOFF_MAX_SECONDS,
    stop_grace_seconds: float = STOP_GRACE_SECONDS,
    status_retry_seconds: float = STATUS_RETRY_SECONDS,
) -> ChildJobResult:
    """Run ``python -m module`` until it exits 0, up to ``backoff_limit`` retries.

    ``still_current`` is asked before every attempt whether the job is still
    this run's to do; False stops without starting the attempt. It does not
    fence an attempt already running.
    """
    deadline = time.monotonic() + deadline_seconds

    def remaining() -> float:
        return deadline - time.monotonic()

    attempts = 0
    while True:
        if _stop_requested.is_set():
            raise ChildJobStopped(module, attempts, None)
        if not await _ask_still_current(
            still_current, remaining, status_retry_seconds, module, attempts
        ):
            return ChildJobResult(attempts=attempts, superseded=True)
        attempts += 1
        exit_code = await _run_once(module, env, remaining(), stop_grace_seconds)
        if exit_code is None:
            raise ChildJobDeadlineExceeded(module, attempts, None)
        if exit_code == 0:
            return ChildJobResult(attempts=attempts, superseded=False)
        if _stop_requested.is_set():
            raise ChildJobStopped(module, attempts, exit_code)
        if attempts > backoff_limit:
            raise ChildJobFailed(module, attempts, exit_code)
        backoff = min(backoff_base_seconds * 2 ** (attempts - 1), backoff_max_seconds)
        if backoff >= remaining():
            raise ChildJobDeadlineExceeded(module, attempts, exit_code)
        _logger.info(
            'child_job.retrying',
            extra={
                'job_module': module,
                'attempts': attempts,
                'exit_code': exit_code,
                'backoff_seconds': backoff,
            },
        )
        await _sleep_unless_stopped(backoff)


async def stop_all_children(stop_grace_seconds: float = STOP_GRACE_SECONDS) -> None:
    """Stop every running child's process group, and start no further attempt.

    For a forced stop of the worker: each running job then ends with
    ChildJobStopped, so its slot is freed only once its child is gone.
    """
    _stop_requested.set()
    await asyncio.gather(
        *(_stop(process, stop_grace_seconds) for process in list(_live))
    )


async def _sleep_unless_stopped(seconds: float) -> None:
    try:
        await asyncio.wait_for(_stop_requested.wait(), seconds)
    except TimeoutError:
        pass


async def _ask_still_current(
    still_current: Callable[[], Awaitable[bool]],
    remaining: Callable[[], float],
    retry_seconds: float,
    module: str,
    attempts: int,
) -> bool:
    # A failed read never starts an attempt: it is read again until the deadline.
    while True:
        if _stop_requested.is_set():
            raise ChildJobStopped(module, attempts, None)
        if remaining() <= 0:
            raise ChildJobDeadlineExceeded(module, attempts, None)
        try:
            return await asyncio.wait_for(still_current(), remaining())
        except Exception as error:
            # Only the deadline's own timeout ends the job; a TimeoutError raised
            # by the read itself (a database timeout) is a failed read.
            if isinstance(error, TimeoutError) and remaining() <= 0:
                raise ChildJobDeadlineExceeded(module, attempts, None) from None
            _logger.warning(
                'child_job.status_read_failed',
                extra={'job_module': module},
                exc_info=True,
            )
        await _sleep_unless_stopped(min(retry_seconds, max(remaining(), 0)))


async def _run_once(
    module: str, env: Mapping[str, str], timeout: float, stop_grace_seconds: float
) -> int | None:
    """One attempt's exit code, or None when the deadline stopped it."""
    # Spawned as a task the cancellation cannot interrupt, so a child that has
    # started is always seen here and stopped.
    spawning = asyncio.ensure_future(
        asyncio.create_subprocess_exec(
            sys.executable,
            '-m',
            module,
            env=dict(env),
            # Its own process group, so stopping it also stops anything it started.
            start_new_session=True,
        )
    )
    try:
        process = await asyncio.shield(spawning)
        _live.add(process)
        return await asyncio.wait_for(process.wait(), timeout)
    except TimeoutError:
        # A cancellation during the stop must not free the slot before the child
        # is gone, so it is held back and raised once the child has exited.
        _, cancelled = await _despite_cancellation(
            asyncio.ensure_future(_stop(process, stop_grace_seconds))
        )
        if cancelled:
            raise asyncio.CancelledError
        return None
    except asyncio.CancelledError:
        # The worker is cancelling the job. Hold its slot until the child is gone,
        # even if the cancellation is repeated.
        process, _ = await _despite_cancellation(spawning)
        await _despite_cancellation(
            asyncio.ensure_future(_stop(process, stop_grace_seconds))
        )
        raise
    finally:
        if spawning.done() and not spawning.cancelled() and not spawning.exception():
            _live.discard(spawning.result())


async def _despite_cancellation[T](task: asyncio.Future[T]) -> tuple[T, bool]:
    """The task's result, and whether this was cancelled while waiting for it."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled


async def _stop(process: asyncio.subprocess.Process, grace_seconds: float) -> None:
    if process.returncode is not None:
        return
    _signal_group(process.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), grace_seconds)
    except TimeoutError:
        pass
    # Also kills anything left in the group after the child itself exited.
    _signal_group(process.pid, signal.SIGKILL)
    await process.wait()


def _signal_group(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass
