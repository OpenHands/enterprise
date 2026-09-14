"""Presence assertions that retain the type of fixture results."""


def present[T](value: T | None) -> T:
    assert value is not None
    return value
