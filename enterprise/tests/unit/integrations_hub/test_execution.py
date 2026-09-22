from __future__ import annotations

import asyncio
import threading

from integrations_hub.execution import BlockingExecutors


def test_readiness_uses_a_worker_separate_from_saturated_request_work() -> None:
    """Connector saturation must not queue the readiness database probe."""
    request_started = threading.Event()
    release_request = threading.Event()
    executors = BlockingExecutors(
        max_concurrent_requests=1,
        readiness_timeout_seconds=0.1,
    )

    def block_request() -> None:
        request_started.set()
        assert release_request.wait(timeout=1)

    async def exercise() -> None:
        blocked_request = asyncio.create_task(executors.run(block_request))
        assert await asyncio.to_thread(request_started.wait, 1)
        try:
            assert await executors.readiness(lambda: True) is True
        finally:
            release_request.set()
        await blocked_request

    try:
        asyncio.run(exercise())
    finally:
        executors.shutdown()


def test_readiness_returns_unready_when_database_check_exceeds_deadline() -> None:
    """An indefinitely slow database check must not hold the ASGI event loop."""
    check_started = threading.Event()
    release_check = threading.Event()
    check_finished = threading.Event()
    executors = BlockingExecutors(
        max_concurrent_requests=1,
        readiness_timeout_seconds=0.01,
    )

    def slow_database_check() -> bool:
        check_started.set()
        assert release_check.wait(timeout=1)
        check_finished.set()
        return True

    async def exercise() -> None:
        readiness = asyncio.create_task(executors.readiness(slow_database_check))
        assert await asyncio.to_thread(check_started.wait, 1)
        assert await readiness is None
        release_check.set()
        assert await asyncio.to_thread(check_finished.wait, 1)

    try:
        asyncio.run(exercise())
    finally:
        executors.shutdown()
