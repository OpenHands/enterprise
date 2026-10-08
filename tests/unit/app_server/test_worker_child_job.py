"""A job's module runs in a supervised child process (worker/child_job.py)."""

import asyncio
import os
import textwrap
import time
from pathlib import Path

import pytest

from openhands.app_server.worker.child_job import (
    ChildJobDeadlineExceeded,
    ChildJobFailed,
    run_child_job,
)

# Short timings so the tests run in seconds; production uses a Job's backoff.
FAST = {
    'backoff_base_seconds': 0.05,
    'backoff_max_seconds': 0.2,
    'stop_grace_seconds': 0.5,
    'status_retry_seconds': 0.05,
}


async def always_current() -> bool:
    return True


def _module(tmp_path: Path, name: str, body: str) -> dict[str, str]:
    """Write ``name`` as an importable module; returns the env that finds it."""
    (tmp_path / f'{name}.py').write_text(textwrap.dedent(body))
    return {**os.environ, 'PYTHONPATH': str(tmp_path), 'STATE_DIR': str(tmp_path)}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _gone(pid: int, within: float = 3.0) -> bool:
    # A killed grandchild is reaped by init, not by us, so give it a moment.
    stop = time.monotonic() + within
    while time.monotonic() < stop:
        if not _alive(pid):
            return True
        await asyncio.sleep(0.05)
    return False


# Exits non-zero until it has run `FAIL_TIMES` times.
FLAKY = """
    import os, pathlib, sys
    counter = pathlib.Path(os.environ['STATE_DIR']) / 'runs'
    runs = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(runs))
    sys.exit(0 if runs > int(os.environ['FAIL_TIMES']) else 3)
"""

# Records its pid, starts a grandchild that records its own pid, then hangs.
HANGS = """
    import os, pathlib, subprocess, sys, time
    state = pathlib.Path(os.environ['STATE_DIR'])
    if os.environ.get('IGNORE_SIGTERM'):
        import signal
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    grandchild = subprocess.Popen(
        [sys.executable, '-c', 'import signal, time; '
         'signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)']
    )
    (state / 'grandchild.pid').write_text(str(grandchild.pid))
    (state / 'child.pid').write_text(str(os.getpid()))
    time.sleep(600)
"""


async def _pids(tmp_path: Path) -> tuple[int, int]:
    for _ in range(200):
        if (tmp_path / 'child.pid').exists():
            break
        await asyncio.sleep(0.05)
    return (
        int((tmp_path / 'child.pid').read_text()),
        int((tmp_path / 'grandchild.pid').read_text()),
    )


async def test_a_job_that_exits_0_runs_once(tmp_path):
    env = _module(tmp_path, 'job_ok', FLAKY) | {'FAIL_TIMES': '0'}

    result = await run_child_job(
        'job_ok',
        env=env,
        deadline_seconds=30,
        backoff_limit=3,
        still_current=always_current,
        **FAST,
    )

    assert (result.attempts, result.superseded) == (1, False)


async def test_a_failed_attempt_is_retried_until_one_succeeds(tmp_path):
    env = _module(tmp_path, 'job_flaky', FLAKY) | {'FAIL_TIMES': '2'}

    result = await run_child_job(
        'job_flaky',
        env=env,
        deadline_seconds=30,
        backoff_limit=3,
        still_current=always_current,
        **FAST,
    )

    assert result.attempts == 3
    assert (tmp_path / 'runs').read_text() == '3'


async def test_the_job_fails_after_backoff_limit_retries(tmp_path):
    env = _module(tmp_path, 'job_bad', FLAKY) | {'FAIL_TIMES': '99'}

    with pytest.raises(ChildJobFailed) as failed:
        await run_child_job(
            'job_bad',
            env=env,
            deadline_seconds=30,
            backoff_limit=2,
            still_current=always_current,
            **FAST,
        )

    assert (failed.value.attempts, failed.value.exit_code) == (3, 3)
    assert (tmp_path / 'runs').read_text() == '3'


async def test_the_deadline_stops_the_child_and_its_group_and_is_not_retried(
    tmp_path,
):
    env = _module(tmp_path, 'job_hangs', HANGS)

    with pytest.raises(ChildJobDeadlineExceeded) as exceeded:
        await run_child_job(
            'job_hangs',
            env=env,
            deadline_seconds=2,
            backoff_limit=3,
            still_current=always_current,
            **FAST,
        )

    assert exceeded.value.attempts == 1
    child, grandchild = await _pids(tmp_path)
    assert await _gone(child)
    assert await _gone(grandchild)


async def test_a_child_that_ignores_sigterm_is_killed_after_the_grace_period(
    tmp_path,
):
    env = _module(tmp_path, 'job_stubborn', HANGS) | {'IGNORE_SIGTERM': '1'}

    started = time.monotonic()
    with pytest.raises(ChildJobDeadlineExceeded):
        await run_child_job(
            'job_stubborn',
            env=env,
            deadline_seconds=2,
            backoff_limit=0,
            still_current=always_current,
            **FAST,
        )

    assert time.monotonic() - started < 2 + FAST['stop_grace_seconds'] + 2
    child, grandchild = await _pids(tmp_path)
    assert await _gone(child)
    assert await _gone(grandchild)


async def test_backoff_that_would_pass_the_deadline_ends_the_job(tmp_path):
    env = _module(tmp_path, 'job_slow_retry', FLAKY) | {'FAIL_TIMES': '99'}

    started = time.monotonic()
    with pytest.raises(ChildJobDeadlineExceeded) as exceeded:
        await run_child_job(
            'job_slow_retry',
            env=env,
            deadline_seconds=5,
            backoff_limit=3,
            still_current=always_current,
            **FAST | {'backoff_base_seconds': 60, 'backoff_max_seconds': 60},
        )

    assert (exceeded.value.attempts, exceeded.value.exit_code) == (1, 3)
    assert time.monotonic() - started < 5


async def test_a_job_no_longer_current_starts_no_attempt(tmp_path):
    env = _module(tmp_path, 'job_ok', FLAKY) | {'FAIL_TIMES': '0'}

    async def superseded() -> bool:
        return False

    result = await run_child_job(
        'job_ok',
        env=env,
        deadline_seconds=30,
        backoff_limit=3,
        still_current=superseded,
        **FAST,
    )

    assert (result.attempts, result.superseded) == (0, True)
    assert not (tmp_path / 'runs').exists()


async def test_a_job_superseded_after_a_failure_starts_no_retry(tmp_path):
    env = _module(tmp_path, 'job_flaky', FLAKY) | {'FAIL_TIMES': '99'}
    answers = iter([True, False])

    async def current_once() -> bool:
        return next(answers)

    result = await run_child_job(
        'job_flaky',
        env=env,
        deadline_seconds=30,
        backoff_limit=3,
        still_current=current_once,
        **FAST,
    )

    assert (result.attempts, result.superseded) == (1, True)
    assert (tmp_path / 'runs').read_text() == '1'


async def test_a_failed_status_read_is_retried_and_never_starts_an_attempt(
    tmp_path,
):
    env = _module(tmp_path, 'job_ok', FLAKY) | {'FAIL_TIMES': '0'}
    reads: list[str] = []

    async def flaky_read() -> bool:
        reads.append('read')
        if len(reads) < 3:
            assert not (tmp_path / 'runs').exists()
            raise ConnectionError('database unavailable')
        return True

    result = await run_child_job(
        'job_ok',
        env=env,
        deadline_seconds=30,
        backoff_limit=3,
        still_current=flaky_read,
        **FAST,
    )

    assert len(reads) == 3
    assert result.attempts == 1


async def test_a_status_read_that_keeps_failing_ends_at_the_deadline(tmp_path):
    env = _module(tmp_path, 'job_ok', FLAKY) | {'FAIL_TIMES': '0'}

    async def broken_read() -> bool:
        raise ConnectionError('database unavailable')

    with pytest.raises(ChildJobDeadlineExceeded) as exceeded:
        await run_child_job(
            'job_ok',
            env=env,
            deadline_seconds=1,
            backoff_limit=3,
            still_current=broken_read,
            **FAST,
        )

    assert exceeded.value.attempts == 0
    assert not (tmp_path / 'runs').exists()


async def test_cancelling_the_job_stops_the_child_before_the_cancel_returns(
    tmp_path,
):
    # It ignores SIGTERM, so it lives until the grace period's SIGKILL.
    env = _module(tmp_path, 'job_hangs', HANGS) | {'IGNORE_SIGTERM': '1'}
    job = asyncio.create_task(
        run_child_job(
            'job_hangs',
            env=env,
            deadline_seconds=60,
            backoff_limit=3,
            still_current=always_current,
            **FAST,
        )
    )
    child, grandchild = await _pids(tmp_path)

    job.cancel()
    # A second cancellation must not free the slot before the child is gone.
    await asyncio.sleep(0.05)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job

    assert not _alive(child)
    assert await _gone(grandchild)


async def test_a_blocking_child_does_not_block_the_event_loop(tmp_path):
    env = _module(
        tmp_path,
        'job_busy',
        'import time\nstart = time.time()\nwhile time.time() - start < 1: pass\n',
    )
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    ticker = asyncio.create_task(tick())
    await run_child_job(
        'job_busy',
        env=env,
        deadline_seconds=30,
        backoff_limit=0,
        still_current=always_current,
        **FAST,
    )
    ticker.cancel()

    assert ticks >= 10
