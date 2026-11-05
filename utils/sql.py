"""Shared SQL helpers."""


def escape_ilike(value: str) -> str:
    """Escape LIKE/ILIKE metacharacters so a search term matches itself.

    The backslash replacement must come first, or the backslashes introduced by
    the `%` and `_` replacements get escaped a second time.

    Callers pass `escape='\\\\'` to `ilike()`. On Postgres that is already the
    default escape character, so it documents the intent rather than changing
    the behaviour — but it is what makes the escaping explicit at the call site.
    """
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
