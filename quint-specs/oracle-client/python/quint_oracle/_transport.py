"""HTTP to the oracle daemon over urllib; the daemon URL is latched at import.

Error policy (shared by every oracle client): an event POST failure — rejected
by the daemon or unreachable — raises with ``METHOD url -> status: body``; the
completion PATCH swallows errors; an unset URL means everything is inert, never
an error.
"""

import contextlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL_VERSION = 1
"""The oracle wire-protocol version this client speaks."""

BASE_URL = os.environ.get('QUINT_ORACLE_URL') or None
"""Latched once, at first import: quint_oracle is inert for the whole process
when QUINT_ORACLE_URL is unset here."""


def enabled() -> bool:
    """Whether a daemon URL was latched at import."""
    return BASE_URL is not None


def post_event(test: str, body: dict) -> None:
    """POST one event body to ``/test/{name}``.

    Raises ``RuntimeError`` when the daemon rejects the event (it is alive and
    refusing — always an instrumentation bug) or is unreachable.
    """
    _send('POST', test, body)


def patch_status(test: str, *, failed: bool) -> None:
    """PATCH the run's outcome; errors are swallowed — outcome reporting runs
    in teardown paths where raising would mask the test's own result."""
    with contextlib.suppress(Exception):
        _send('PATCH', test, {'status': 'failed' if failed else 'ok'})


def _send(method: str, test: str, body: dict) -> None:
    if BASE_URL is None:
        return  # env unset: no-op
    url = f'{BASE_URL}/test/{encode_path(test)}'
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method=method,
        headers={
            'Quint-Oracle-Protocol': str(PROTOCOL_VERSION),
            'Content-Type': 'application/json',
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            response.read()  # drain, so the connection closes cleanly
    except urllib.error.HTTPError as error:
        text = error.read().decode(errors='replace').strip()
        raise RuntimeError(f'{method} {url} -> {error.code}: {text}') from None
    except urllib.error.URLError as error:
        raise RuntimeError(f'{method} {url} failed: {error.reason}') from None


def encode_path(segment: str) -> str:
    """Percent-encode one URL path segment: RFC 3986 unreserved bytes stay raw,
    everything else — ``/``, ``%``, spaces, non-ASCII bytes — is ``%XX``-escaped
    byte-wise over UTF-8.

    ``quote(..., safe="")`` is exactly that rule (letters, digits, ``_.-~``);
    the daemon percent-decodes the segment back into bytes and reads them as
    UTF-8, so a name round-trips intact.
    """
    return urllib.parse.quote(segment.encode(), safe='')
