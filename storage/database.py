"""
Database connection module for enterprise storage.

This is for backwards compatibility with V0.

This module provides database engines and session makers by delegating to the
centralized DbSessionInjector from app_server/config.py. This ensures a single
source of truth for database connection configuration.
"""

import contextlib


def sqlstate(exc: BaseException) -> str | None:
    """Return the SQLSTATE code carried by a database exception, if any.

    Drivers disagree on whether the code lands on ``sqlstate`` or ``pgcode``,
    so both are checked.
    """
    orig = getattr(exc, 'orig', None)
    for attr in ('sqlstate', 'pgcode'):
        value = getattr(orig, attr, None)
        if isinstance(value, str):
            return value
    return None


def _get_db_session_injector():
    from openhands.app_server.config import get_global_config

    _config = get_global_config()
    return _config.db_session


def session_maker(**kwargs):
    db_session_injector = _get_db_session_injector()
    factory = db_session_injector.get_session_maker()
    return factory(**kwargs)


@contextlib.asynccontextmanager
async def a_session_maker(**kwargs):
    db_session_injector = _get_db_session_injector()
    factory = await db_session_injector.get_async_session_maker()
    async with factory(**kwargs) as session:
        yield session


def get_engine():
    db_session_injector = _get_db_session_injector()
    engine = db_session_injector.get_db_engine()
    return engine
