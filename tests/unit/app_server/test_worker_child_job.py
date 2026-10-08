"""A job's module runs in a supervised child process (worker/child_job.py)."""

import asyncio
import os
import textwrap
import time
from pathlib import Path

import pytest

from openhands.app_server.worker import child_job
from openhands.app_server.worker.child_job import (
    ChildJobDeadlineExceeded,
    ChildJobFailed,
    run_child_job,
)

# A broken stop path hangs on a child that never exits; fail it instead.
pytestmark = pytest.mark.timeout(30)

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


# Exits 3 after a short run, so several attempts together outlast the deadline.
SLOW_FAIL = """
    import sys, time
    time.sleep(0.8)
    sys.exit(3)
"""

# Its SIGTERM handler waits for a grandchild that exits on its own SIGTERM.
GRACEFUL = """
    import os, pathlib, signal, subprocess, sys, time
    state = pathlib.Path(os.environ['STATE_DIR'])
    grandchild = subprocess.Popen([sys.executable, '-c', (
        'import pathlib, signal, sys, time\\n'
        'def term(*_):\\n'
        f'    pathlib.Path({str(state)!r}, "grandchild.term").write_text("1")\\n'
        '    sys.exit(0)\\n'
        'signal.signal(signal.SIGTERM, term)\\n'
        f'pathlib.Path({str(state)!r}, "grandchild.ready").write_text("1")\\n'
        'time.sleep(600)\\n'
    )])
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(grandchild.wait()))
    while not (state / 'grandchild.ready').exists():
        time.sleep(0.01)
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


async def test_one_deadline_covers_every_attempt(tmp_path):
    env = _module(tmp_path, 'job_slow_fail', SLOW_FAIL)

    with pytest.raises(ChildJobDeadlineExceeded) as exceeded:
        await run_child_job(
            'job_slow_fail',
            env=env,
            deadline_seconds=1.2,
            backoff_limit=5,
            still_current=always_current,
            **FAST,
        )

    # The second attempt gets only what the first left, so the deadline stops it.
    assert (exceeded.value.attempts, exceeded.value.exit_code) == (2, None)


async def test_backoff_doubles_up_to_the_cap(tmp_path, monkeypatch):
    env = _module(tmp_path, 'job_bad', FLAKY) | {'FAIL_TIMES': '99'}
    backoffs: list[float] = []
    monkeypatch.setattr(
        child_job._logger,
        'info',
        lambda msg, extra: backoffs.append(extra['backoff_seconds']),
    )

    with pytest.raises(ChildJobFailed):
        await run_child_job(
            'job_bad',
            env=env,
            deadline_seconds=30,
            backoff_limit=4,
            still_current=always_current,
            **FAST | {'backoff_base_seconds': 0.01, 'backoff_max_seconds': 0.04},
        )

    assert backoffs == [0.01, 0.02, 0.04, 0.04]


async def test_the_deadline_sends_sigterm_to_the_whole_group(tmp_path):
    env = _module(tmp_path, 'job_graceful', GRACEFUL)

    with pytest.raises(ChildJobDeadlineExceeded):
        await run_child_job(
            'job_graceful',
            env=env,
            deadline_seconds=2,
            backoff_limit=0,
            still_current=always_current,
            **FAST,
        )

    assert (tmp_path / 'grandchild.term').exists()


async def test_a_status_read_that_hangs_ends_at_the_deadline(tmp_path):
    env = _module(tmp_path, 'job_ok', FLAKY) | {'FAIL_TIMES': '0'}

    async def hung_read() -> bool:
        await asyncio.sleep(600)
        return True

    with pytest.raises(ChildJobDeadlineExceeded) as exceeded:
        await asyncio.wait_for(
            run_child_job(
                'job_ok',
                env=env,
                deadline_seconds=0.5,
                backoff_limit=3,
                still_current=hung_read,
                **FAST,
            ),
            5,
        )

    assert exceeded.value.attempts == 0
    assert not (tmp_path / 'runs').exists()


async def test_a_wall_clock_step_does_not_end_the_job(tmp_path, monkeypatch):
    env = _module(tmp_path, 'job_ok', FLAKY) | {'FAIL_TIMES': '0'}
    # An NTP step a day forward after the first reading.
    readings = iter([0.0])
    monkeypatch.setattr(time, 'time', lambda: next(readings, 86400.0))

    result = await run_child_job(
        'job_ok',
        env=env,
        deadline_seconds=30,
        backoff_limit=0,
        still_current=always_current,
        **FAST,
    )

    assert result.attempts == 1
