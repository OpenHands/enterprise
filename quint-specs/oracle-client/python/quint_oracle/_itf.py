"""Bridge from Python values onto the ITF JSON dialect the oracle parses.

The automatic conversions are capped at exactly what the Rust reference client
converts (its ``ToLogged`` impl list, nothing beyond it): ``bool`` and ``str``
bare; ``int`` bare within i64, otherwise ``{"#bigint": "..."}``; ``list`` →
array; ``set``/``frozenset`` → ``#set``; ``dict`` → ``#map`` — the collection
forms ordered deterministically by encoded form. Everything else is rejected
by name: hand-build those shapes with :mod:`quint_oracle.itf` or a ``to_itf()``
method on the domain type.
"""

from __future__ import annotations

import enum
import json

_I64_MIN = -(2**63)
_I64_MAX = 2**63 - 1


class Value:
    """An already-encoded ITF value; the encoder sends its data as-is.

    Built by the :mod:`quint_oracle.itf` constructors, or returned from a
    domain type's ``to_itf()`` method.
    """

    __slots__ = ('data',)

    def __init__(self, data: object) -> None:
        self.data = data

    def __repr__(self) -> str:
        return f'Value({self.data!r})'


def encode(value: object) -> object:
    """Encode ``value`` as a JSON-serializable ITF value.

    A ``to_itf()`` method is checked first, so domain types can hand-build
    their own shape; a zero-arg callable is a lazy thunk, invoked here — which
    only happens when the oracle is live. Raises ``TypeError`` on any kind the
    dialect cannot carry automatically.
    """
    if isinstance(value, Value):
        return value.data
    to_itf = getattr(value, 'to_itf', None)
    if callable(to_itf):
        return encode(to_itf())
    if isinstance(value, enum.Enum):
        # Before the int check: IntEnum members are ints.
        _reject(value)
    if isinstance(value, bool):
        # Before the int check: bool subclasses int.
        return value
    if isinstance(value, int):
        if _I64_MIN <= value <= _I64_MAX:
            return value
        return {'#bigint': str(value)}
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [encode(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return {'#set': sorted((encode(item) for item in value), key=canonical)}
    if isinstance(value, dict):
        pairs = [[encode(key), encode(item)] for key, item in value.items()]
        return {'#map': sorted(pairs, key=lambda pair: canonical(pair[0]))}
    if callable(value):
        return encode(value())  # zero-arg lazy thunk
    _reject(value)


def _reject(value: object):
    raise TypeError(
        f'cannot log a value of type {type(value).__name__}: the oracle '
        'auto-converts bool, str, int, list, set, and dict only — hand-build '
        'other shapes with quint_oracle.itf or a to_itf() method'
    )


def canonical(data: object) -> str:
    """The sort key that makes ``#set``/``#map`` ordering deterministic."""
    return json.dumps(data, sort_keys=True, separators=(',', ':'))


def render_path(segments) -> list[str]:
    """Render assertion path segments to the daemon's string forms: strings
    raw (unquoted), ints as decimal. Anything else — bool included, and ints
    beyond i64, whose ``#bigint`` form the daemon's key parse cannot match —
    is rejected."""
    rendered = []
    for segment in segments:
        if isinstance(segment, str):
            rendered.append(segment)
        elif (
            isinstance(segment, int)
            and not isinstance(segment, bool)
            and _I64_MIN <= segment <= _I64_MAX
        ):
            rendered.append(str(segment))
        else:
            raise TypeError(
                'an assertion path segment must be a string or an int within '
                f'i64 — the daemon resolves no other key shape; got {segment!r}'
            )
    return rendered
