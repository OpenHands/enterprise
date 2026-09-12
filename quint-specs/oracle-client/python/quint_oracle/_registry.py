"""Which test an observation belongs to, and how a run's outcome is reported.

Attribution state is threefold: a ``contextvars.ContextVar`` holding the
current test (asyncio tasks inherit it at creation, so ``log()`` inside a task
resolves without help), a process-global registry of open runs (registered,
not yet reported), and the sole-open-run fallback for worker threads that
inherited no context — which errors loudly when more than one run is open,
never mis-attributing.
"""

from __future__ import annotations

import threading
from contextvars import ContextVar, Token

from . import _transport


class TestHandle:
    """Cross-thread test identity: capture it with ``current_test()`` and hand
    it to ``event()`` from worker threads and pools.

    Identity, not name equality: two runs of the same test name are distinct
    handles.
    """

    __slots__ = ('_name',)

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        """The test name the daemon buffers this run's trace under."""
        return self._name

    def __repr__(self) -> str:
        return f'TestHandle({self._name!r})'


_current: ContextVar[TestHandle | None] = ContextVar(
    'quint_oracle_current_test', default=None
)
_lock = threading.Lock()
_open: list[TestHandle] = []


class TestGuard:
    """Owner of one run's status report: ``close()`` signals completion to the
    daemon. Inert (every method a no-op) when the oracle is disabled."""

    __slots__ = ('_failed', '_handle', '_token')

    def __init__(self, handle: TestHandle | None, token: Token | None) -> None:
        self._handle = handle
        self._token = token
        self._failed = False

    def set_failed(self) -> None:
        """Report this run as failed — discarding its trace — regardless of
        anything else; ``close()`` still must be called."""
        self._failed = True

    def close(self) -> None:
        """Report the run's outcome. Idempotent: only the first call reports."""
        handle, self._handle = self._handle, None
        if handle is None:
            return
        # Leave the open registry FIRST, so a late worker event errors rather
        # than attach to a finalized run.
        with _lock:
            _open[:] = [entry for entry in _open if entry is not handle]
        _transport.patch_status(handle.name, failed=self._failed)
        token, self._token = self._token, None
        if token is not None:
            try:
                _current.reset(token)  # restore the enclosing registration
            except ValueError:
                # Closed in a different context than register_test() ran in:
                # clear only our own registration.
                if _current.get() is handle:
                    _current.set(None)


def register_test(name: str) -> TestGuard:
    """Open a run named ``name`` and make it this context's current test;
    the returned guard reports the outcome on ``close()``.

    Registering a name the oracle has already seen completed opens a fresh
    run rather than appending to the previous trace. No-op (inert guard) when
    the oracle is disabled.
    """
    if not _transport.enabled():
        return TestGuard(None, None)
    handle = TestHandle(str(name))
    with _lock:
        _open.append(handle)
    return TestGuard(handle, _current.set(handle))


def current_test() -> TestHandle:
    """The test this context's observations belong to. Resolution order:

    1. this context's registration (the pytest plugin's, ``register_test()``,
       or ``quint_oracle.test(...)``) — asyncio tasks inherit it at creation;
    2. a worker thread with no inherited context: the sole open run, iff
       exactly one is open;
    3. otherwise ``RuntimeError`` — never a guess.

    Raises when the oracle is not live (unlike ``register_test``, which stays
    inert there): an unguarded call site is a programming error — gate on
    ``enabled()``; ``log()`` effectively is, being a no-op binding.
    """
    if not _transport.enabled():
        raise RuntimeError(
            'current_test() needs a live oracle — guard call sites with '
            'quint_oracle.enabled()'
        )
    current = _current.get()
    if current is not None:
        return current
    with _lock:
        if len(_open) == 1:
            return _open[0]
        count = len(_open)
    raise RuntimeError(
        f'cannot attribute this event: {count} tests are open and this '
        'context has no test; register through the pytest plugin, '
        'register_test(), or pass a TestHandle to event()'
    )
