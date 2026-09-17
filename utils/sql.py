"""Shared SQL helpers."""


def escape_ilike(value: str) -> str:
    """Escape LIKE/ILIKE metacharacters so a search term matches itself.

    The backslash replacement must come first, or the backslashes introduced by
    the `%` and `_` replacements get escaped a second time.

    Callers must pass `escape='\\\\'` to `ilike()`; without it the backslashes
    reach the database as literal characters and the escaping does nothing.
    """
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
