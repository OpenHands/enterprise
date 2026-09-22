"""Bounded worker pools for legacy synchronous backend work."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from typing import Any


@dataclass
class BlockingExecutors:
    """Keep blocking request work from monopolizing FastAPI's event loop.

    The request pool is deliberately separate from the single-worker readiness
    pool. Saturated connector calls should queue normal work, but must not make
    an otherwise healthy process fail its liveness probes or prevent readiness
    from checking the database.
    """

    max_concurrent_requests: int
    readiness_timeout_seconds: float
    _request_executor: ThreadPoolExecutor = field(init=False)
    _readiness_executor: ThreadPoolExecutor = field(init=False)

    def __post_init__(self) -> None:
        self._request_executor = ThreadPoolExecutor(
            max_workers=self.max_concurrent_requests,
            thread_name_prefix="integrations-hub-request",
        )
        self._readiness_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="integrations-hub-readiness",
        )

    async def run[T](self, func: Callable[..., T], *args: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._request_executor, partial(func, *args))

    async def readiness[T](self, func: Callable[..., T], *args: Any) -> T | None:
        """Return ``None`` when a readiness operation exceeds its deadline."""
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(self._readiness_executor, partial(func, *args)),
                timeout=self.readiness_timeout_seconds,
            )
        except TimeoutError:
            return None

    def shutdown(self) -> None:
        """Stop accepting queued work during application shutdown."""
        self._request_executor.shutdown(wait=False, cancel_futures=True)
        self._readiness_executor.shutdown(wait=False, cancel_futures=True)
