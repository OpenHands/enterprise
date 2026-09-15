"""Hand-built ITF values, one constructor per form the oracle parses.

Mirrors the reference client's ``itf::Value``: the automatic encoding (see
:mod:`quint_oracle._itf`) covers bool/str/int/list/set/dict; every other shape
— records, tuples — is built explicitly here, or returned from a domain type's
``to_itf()`` method::

    class Account:
        def to_itf(self):
            return itf.record(owner=self.owner, balance=self.balance)

A payload-free variant is logged as its name, a plain string — there are no
variant helpers, by design.
"""

from __future__ import annotations

from ._itf import Value, canonical, encode

__all__ = [
    'Value',
    'boolean',
    'integer',
    'list_',
    'map_',
    'record',
    'set_',
    'string',
    'tup',
]


def boolean(value: bool) -> Value:
    """A bare boolean."""
    return Value(bool(value))


def string(value: str) -> Value:
    """A bare string."""
    if not isinstance(value, str):
        raise TypeError(f'itf.string() takes a str; got {type(value).__name__}')
    return Value(value)


def integer(value: int) -> Value:
    """An integer: bare within i64, the ``#bigint`` form beyond."""
    return Value(encode(_int(value)))


def list_(*values) -> Value:
    """A list (a JSON array)."""
    return Value([encode(value) for value in values])


def set_(*values) -> Value:
    """A ``{"#set": [...]}``, deterministically ordered by encoded form."""
    return Value({'#set': sorted((encode(value) for value in values), key=canonical)})


def map_(*pairs) -> Value:
    """A ``{"#map": [[k, v], ...]}`` from ``(key, value)`` pairs,
    deterministically ordered by encoded key. Keys may be any ITF value —
    unlike a ``dict``, which auto-encodes to ``#map`` but limits key shapes to
    hashable Python values."""
    encoded = [[encode(key), encode(value)] for key, value in pairs]
    return Value({'#map': sorted(encoded, key=lambda pair: canonical(pair[0]))})


def tup(*values) -> Value:
    """A ``{"#tup": [...]}`` tuple."""
    return Value({'#tup': [encode(value) for value in values]})


def record(**fields) -> Value:
    """A record (a JSON object), field order preserved."""
    return Value({name: encode(value) for name, value in fields.items()})


def _int(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f'expected an int; got {type(value).__name__}')
    return value
