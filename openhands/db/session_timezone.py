"""Pin PostgreSQL sessions to UTC, shared by app and migration database setup.

Several tables keep ``timestamp without time zone`` columns that the models bind
with timezone-aware UTC values. PostgreSQL converts such a value to the session
time zone before dropping the offset, while every reader treats the stored
wall-clock as UTC, so a server whose default ``timezone`` is not UTC skews each
write by its offset. The zone is sent as a connection startup parameter: unlike
``SET TIME ZONE`` issued after connecting, a startup parameter is the session
default, so a rollback (including the pool's reset on return) cannot undo it.
"""

DB_SESSION_TIMEZONE = 'UTC'


def build_asyncpg_timezone_args() -> dict[str, dict[str, str]]:
    return {'server_settings': {'timezone': DB_SESSION_TIMEZONE}}


def build_pg8000_timezone_args() -> dict[str, dict[str, str]]:
    return {'startup_params': {'timezone': DB_SESSION_TIMEZONE}}


def build_libpq_timezone_args() -> dict[str, str]:
    """For psycopg2, which takes libpq ``options`` instead of a parameter map."""
    return {'options': f'-c timezone={DB_SESSION_TIMEZONE}'}
