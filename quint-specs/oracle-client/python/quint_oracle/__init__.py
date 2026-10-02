"""Instrumentation client for the Quint Studio oracle: logs what your tests
actually do, so Quint Studio can validate those observations against a Quint
specification.

The model
=========

An *observation* is one action your code took, logged at the moment it
happened: an action name, the values involved (each one named, optionally
tagged with the spec constant — its *domain* — the value is added to),
optional post-state *assertions* (what a spec variable must now contain), and
the component *scopes* the action belongs to. Observations logged during one
test form that test's *trace*; the oracle buffers each trace under the test's
name and, when the test finishes successfully, replays it against the spec. A
test that fails (or raises) has its trace discarded.

Each execution of a test is one *run*: registering the same test name again
(a parametrized case, a loop of ``quint_oracle.test(...)`` blocks) opens a
fresh run rather than appending to the previous trace.

The no-op guarantee
===================

This package is **inert by default**: ``QUINT_ORACLE_URL`` is read once, when
the interpreter first imports ``quint_oracle``, and when it is unset the public
functions are bound to no-ops — production code carries the calls but they do
nothing and talk to nothing. The consequence: set the env before the first
import. That is always true under Quint Studio's ``quint oracle``, which
spawns the test process with the env already set.

``enabled()`` is the hot-path guard: gate expensive value construction on it,
or pass a zero-arg callable as a value — a lazy thunk, evaluated only when the
oracle is live.

Registering tests
=================

The pytest plugin registers every test under its nodeid and reports its
outcome — zero-config when this package is pip-installed (a ``pytest11`` entry
point); vendored copies add one line to the top-level conftest.py::

    pytest_plugins = ["quint_oracle.pytest_plugin"]

That default cannot see everything. Reach for an escape hatch when it cannot:

===================================================  ===========================
when                                                 use
===================================================  ===========================
the code under test runs outside pytest              ``quint_oracle.test(...)``
                                                     (context manager/decorator)
the run's name or lifetime is not one pytest test's  ``register_test(...)``
failure is detected by your own logic                ``guard.set_failed()``
observations come from worker threads or pools       pass ``current_test()``'s
                                                     handle to ``event(...)``
===================================================  ===========================

Explicit registration takes precedence over the plugin's (the innermost open
registration is the current test), and every registration reports exactly one
run.

Logging observations
====================

Everything goes through :func:`log`: the action first, then scopes and
assertions positionally, then named values as keyword arguments::

    import quint_oracle as oracle

    oracle.log("deposit", "bank", amount=100)

``In(value, "DOMAIN")`` tags a value with the spec constant it is appended to.
Replay collects every value logged under ``ACCOUNTS`` into that constant,
which is how the spec learns your test's actual account names::

    oracle.log("deposit", "bank", account=oracle.In(account, "ACCOUNTS"))

``expect(*path).equals(expected)`` pins the post-state: after this action, the
spec value at ``path`` must equal ``expected``::

    oracle.log(
        "transfer",
        "bank",
        oracle.expect("accounts", sender).equals(900),
        sender=oracle.In(sender, "ACCOUNTS"),
        amount=100,
    )

Finally, an action may belong to several component scopes; the daemon needs at
least one::

    oracle.log("transfer", "bank", "transfers", amount=100)

Values auto-encode when they are bool, str, int, list, set, or dict; other
shapes are hand-built with :mod:`quint_oracle.itf` or a ``to_itf()`` method on
the domain type.
"""

from __future__ import annotations

import contextlib

from . import _itf, _transport, itf
from ._registry import TestGuard, TestHandle, current_test, register_test
from ._transport import PROTOCOL_VERSION

__all__ = [
    'PROTOCOL_VERSION',
    'EventBuilder',
    'Expect',
    'In',
    'TestGuard',
    'TestHandle',
    'current_test',
    'enabled',
    'event',
    'expect',
    'itf',
    'log',
    'register_test',
    'test',
]


def enabled() -> bool:
    """Whether instrumentation is live: ``QUINT_ORACLE_URL`` was set when this
    package was first imported. The hot-path guard for expensive values."""
    return _transport.enabled()


class In:
    """A domain-tagged named value:
    ``log("deposit", "bank", account=In("alice", "ACCOUNTS"))`` tags the value
    with the spec constant it is appended to."""

    __slots__ = ('domain', 'value')

    def __init__(self, value: object, domain: str) -> None:
        self.value = value
        self.domain = str(domain)


class Expect:
    """One post-state assertion, built by ``expect(*path).equals(expected)``.

    A dumb container: the path is validated and the value encoded only when a
    live event sends, so an inert run never evaluates either.
    """

    __slots__ = ('expected', 'path')

    def __init__(self, path: tuple, expected: object) -> None:
        self.path = path
        self.expected = expected


class _Path:
    __slots__ = ('_segments',)

    def __init__(self, segments: tuple) -> None:
        self._segments = segments

    def equals(self, expected: object) -> Expect:
        """The value the spec must hold at this path after the action."""
        return Expect(self._segments, expected)


def expect(*path) -> _Path:
    """Start a post-state assertion: after this action, the spec value at
    ``path`` must ``.equals(...)`` the expected value. Path segments walk from
    a spec variable name through record fields and map keys — strings, or ints
    within i64 — down to the value asserted on::

        oracle.expect("accounts", "alice").equals(900)
    """
    return _Path(path)


class EventBuilder:
    """Accumulates one observation's parts in any order; ``send()`` posts it.

    The un-sugared layer under :func:`log`, and the escape hatch for worker
    threads and pools, which pass the ``TestHandle`` explicitly::

        handle = oracle.current_test()
        pool.submit(
            lambda: oracle.event(handle, "transfer")
            .argument("amount", 100)
            .scope("bank")
            .send()
        )

    Building is cheap but not free — gate call sites on :func:`enabled`.
    """

    def __init__(self, test: TestHandle, action: str) -> None:
        self._test = test
        self._action = str(action)
        self._scopes: list[str] = []
        self._arguments: list[dict] = []
        self._assertions: list[dict] = []

    def argument(self, name: str, value: object, domain: str | None = None):
        """A named value the action involved; encoded immediately, so later
        mutation cannot alter the observation. ``domain`` names the spec
        constant the value is added to."""
        argument = {'name': str(name), 'value': _itf.encode(value)}
        if domain is not None:
            argument['domain'] = str(domain)
        self._arguments.append(argument)
        return self

    def expect(self, assertion: Expect):
        """A post-state assertion, built by
        ``quint_oracle.expect(*path).equals(expected)``."""
        self._assertions.append(
            {
                'path': _itf.render_path(assertion.path),
                'value': _itf.encode(assertion.expected),
            }
        )
        return self

    def scope(self, name: str):
        """A component scope this action belongs to; call once per scope. The
        daemon requires at least one — it rejects a scopeless event."""
        self._scopes.append(str(name))
        return self

    def send(self) -> None:
        """Post the observation. Raises if the daemon rejects it — a refused
        event means the instrumentation is wrong (a missing scope invalidates
        the whole run)."""
        _transport.post_event(
            self._test.name,
            {
                'action': self._action,
                'scopes': self._scopes,
                'arguments': self._arguments,
                'assertions': self._assertions,
            },
        )


class _NoopEventBuilder:
    """Inert twin of :class:`EventBuilder`: chains and sends nothing."""

    def argument(self, name, value, domain=None):
        return self

    def expect(self, assertion):
        return self

    def scope(self, name):
        return self

    def send(self) -> None:
        return None


_NOOP_EVENT_BUILDER = _NoopEventBuilder()


def _live_event(handle: TestHandle, action: str) -> EventBuilder:
    """Start an observation of ``action`` in the test ``handle`` identifies —
    the internal layer under :func:`log`, and the worker-thread escape hatch."""
    return EventBuilder(handle, action)


def _noop_event(handle, action):
    """Inert twin of event(): QUINT_ORACLE_URL was unset at import."""
    return _NOOP_EVENT_BUILDER


def _live_log(action: str, /, *components, **arguments) -> None:
    """Log one observation of ``action`` on the current test.

    Positional components are component scopes (strings — the daemon requires
    at least one) and assertions (``expect(*path).equals(expected)``); keyword
    arguments are the named values involved, each optionally wrapped in
    ``In(value, "DOMAIN")`` — and a zero-arg callable is a lazy thunk,
    evaluated only when the oracle is live::

        oracle.log(
            "transfer",
            "bank",
            oracle.expect("accounts", "alice").equals(900),
            sender=oracle.In("alice", "ACCOUNTS"),
            amount=100,
        )

    Raises if no test can be attributed (see :func:`current_test`) or the
    daemon rejects the event.
    """
    builder = EventBuilder(current_test(), action)
    for component in components:
        if isinstance(component, str):
            builder.scope(component)
        elif isinstance(component, Expect):
            builder.expect(component)
        else:
            raise TypeError(
                'log() components are scope strings or '
                'expect(...).equals(...) assertions; got '
                f'{type(component).__name__}'
            )
    for name, value in arguments.items():
        if isinstance(value, In):
            builder.argument(name, value.value, value.domain)
        else:
            builder.argument(name, value)
    builder.send()


def _noop_log(action, /, *components, **arguments) -> None:
    """Inert twin of log(): QUINT_ORACLE_URL was unset at import."""
    return


# The import-time rebind: one env read decides the process's bindings, so the
# hot path costs a no-op call when the oracle is not configured.
if _transport.enabled():
    log = _live_log
    event = _live_event
else:
    log = _noop_log
    event = _noop_event


class _TestContext(contextlib.ContextDecorator):
    """One explicitly owned run, as a context manager (and, via
    ContextDecorator, a decorator — each call opens a fresh run)."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._guard: TestGuard | None = None

    def __enter__(self) -> TestGuard:
        self._guard = register_test(self._name)
        return self._guard

    def __exit__(self, exc_type, exc, tb) -> bool:
        guard, self._guard = self._guard, None
        if exc_type is not None:
            guard.set_failed()
        guard.close()
        return False


def test(name_or_fn):
    """Explicit run ownership — the escape hatch for code the pytest plugin
    cannot see (scripts, other harnesses, a run per loop iteration).

    As a context manager, ``with oracle.test("case"):`` opens the run, marks
    it failed if the body raises, and reports on exit (yielding the guard, for
    ``guard.set_failed()``). As a decorator — ``@oracle.test`` on a function,
    or ``@oracle.test("name")`` — every call is one run, named by the
    function's qualified name unless given.
    """
    if callable(name_or_fn):
        return _TestContext(name_or_fn.__qualname__)(name_or_fn)
    return _TestContext(str(name_or_fn))
