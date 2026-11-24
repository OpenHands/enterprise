"""Map real runtime identities onto the Quint spec's symbolic names.

The spec picks its identities from ``pure val`` sets — ``USERS``,
``GOOD_PROFILE_NAMES``, ``CONN_IDS`` and friends. Domain tagging
(``quint_oracle.In``) only widens a ``const``, so a logged UUID or a test's
own ``'A'`` is rejected at replay. Instrumentation therefore canonicalises:
the first distinct value a test shows for a domain becomes that domain's
first symbolic name, the second becomes the second, and so on.

The mapping is per test run, because the spec's state starts fresh per
replayed trace — and per domain, so a user id and a profile name that happen
to be the same string do not share a slot.

Inert-safe: with no oracle running, ``current_test()`` raises and everything
falls back to one process-wide scope. Nothing here is imported by production
code paths except through the same guarded import as the client itself.
"""

from __future__ import annotations

import threading

_LOCK = threading.Lock()
_SCOPES: dict[tuple[str, str], dict[str, str]] = {}


def _scope() -> str:
    try:
        import quint_oracle

        return quint_oracle.current_test().name
    except Exception:
        return '<no-test>'


def canon(domain: str, value: object, names: tuple[str, ...]) -> str:
    """Return the spec name this ``value`` stands for in the current test.

    Assignment is first-come within ``(test, domain)``. A test that shows more
    distinct values than ``names`` holds wraps round — the spec's identities
    are interchangeable, so a wrap models two of them landing in one slot
    rather than inventing a name the spec cannot pick.
    """
    key = (_scope(), domain)
    text = '' if value is None else str(value)
    with _LOCK:
        table = _SCOPES.setdefault(key, {})
        if text not in table:
            table[text] = names[len(table) % len(names)]
        return table[text]


def reset() -> None:
    """Drop every mapping. Test-only."""
    with _LOCK:
        _SCOPES.clear()


# The name sets, kept beside the spec's own declarations so the two are
# edited together.
USERS = ('u1', 'u2', 'u3')
PROFILE_NAMES = ('p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7', 'p8', 'p9', 'p10')
AGENT_IDS = ('a1', 'a2', 'a3', 'a4')
AGENT_NAMES = ('ap1', 'ap2', 'ap3', 'ap4')
CONN_IDS = ('c1', 'c2', 'c3', 'c4')
SECRET_NAMES = ('s1', 's2', 's3', 's4', 's5', 's6')
LANGUAGES = ('en', 'ja')
HOSTS = ('h1', 'h2')


ANON = '<anonymous>'


def user(value) -> str:
    """Canonicalise a user id, folding unattributed writes onto the ambient one.

    A store-level write has no request behind it, so it logs ``ANON``. In a
    single-user test that write belongs to the very user the request path is
    exercising, and giving it a slot of its own would split one user in two —
    so ``ANON`` shares the first slot, and a real id claims that slot when it
    is the only one taken.
    """
    text = '' if value is None else str(value)
    key = (_scope(), 'USERS')
    with _LOCK:
        table = _SCOPES.setdefault(key, {})
        if text in table:
            return table[text]
        if text == ANON:
            if table:
                return next(iter(table.values()))
            table[text] = USERS[0]
            return USERS[0]
        if ANON in table and len(table) == 1:
            table[text] = table[ANON]
            return table[text]
        table[text] = USERS[len(set(table.values())) % len(USERS)]
        return table[text]


def profile(value) -> str:
    return canon('GOOD_PROFILE_NAMES', value, PROFILE_NAMES)


def agent_id(value) -> str:
    return canon('AGENT_IDS', value, AGENT_IDS)


def agent_name(value) -> str:
    return canon('AGENT_NAMES', value, AGENT_NAMES)


def conn(value) -> str:
    return canon('CONN_IDS', value, CONN_IDS)


def secret(value) -> str:
    """Secret names, with ``CODEX_AUTH_JSON`` passed through: the spec treats
    that one slot specially, so it keeps its real name."""
    if value == 'CODEX_AUTH_JSON':
        return 'CODEX_AUTH_JSON'
    return canon('BASE_SECRET_NAMES', value, SECRET_NAMES)


def host(value) -> str:
    return canon('HOSTS', value, HOSTS)


def language(value) -> str:
    """ "" is the spec's explicit null; any other code is canonicalised."""
    if not value:
        return ''
    return canon('LANGUAGES', value, LANGUAGES)


def search_limit(value: int) -> int:
    """Clamp a page size onto the boundary values the spec picks from."""
    if value <= 0:
        return 0
    if value == 1:
        return 1
    return 100 if value <= 100 else 101
