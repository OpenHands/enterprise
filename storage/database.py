"""
Database connection module for enterprise storage.

This is for backwards compatibility with V0.

This module provides database engines and session makers by delegating to the
centralized DbSessionInjector from app_server/config.py. This ensures a single
source of truth for database connection configuration.
"""

import contextlib
from collections.abc import AsyncIterator
from typing import Literal, TypedDict, TypeVarTuple, Unpack

from sqlalchemy import CursorResult, Engine, Result
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from openhands.app_server.services.db_session_injector import DbSessionInjector


class _SessionOptions(TypedDict, total=False):
    expire_on_commit: bool
    autoflush: bool
    autocommit: Literal[False]


_ResultColumns = TypeVarTuple('_ResultColumns')


def affected_row_count(result: Result[tuple[Unpack[_ResultColumns]]]) -> int:
    """Read the cursor count returned by SQLAlchemy for non-returning DML."""
    # AsyncSession.execute advertises the general Result interface, while
    # UPDATE/DELETE without RETURNING produce a CursorResult with rowcount.
    if not isinstance(result, CursorResult):
        raise TypeError('Row counts require a cursor result from a DML statement')
    return result.rowcount


def _get_db_session_injector() -> DbSessionInjector:
    from openhands.app_server.config import get_global_config

    _config = get_global_config()
    return _config.db_session


def session_maker(**kwargs: Unpack[_SessionOptions]) -> Session:
    db_session_injector = _get_db_session_injector()
    factory = db_session_injector.get_session_maker()
    return factory(**kwargs)


@contextlib.asynccontextmanager
async def a_session_maker(
    **kwargs: Unpack[_SessionOptions],
) -> AsyncIterator[AsyncSession]:
    db_session_injector = _get_db_session_injector()
    factory = await db_session_injector.get_async_session_maker()
    async with factory(**kwargs) as session:
        yield session


def get_engine() -> Engine:
    db_session_injector = _get_db_session_injector()
    engine = db_session_injector.get_db_engine()
    return engine
