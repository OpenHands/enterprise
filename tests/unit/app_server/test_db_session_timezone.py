"""PostgreSQL sessions opened by ``DbSessionInjector`` are pinned to UTC.

``conversation_metadata.created_at`` / ``last_updated_at`` are ``timestamp
without time zone`` (migration 003) while ``StoredConversationMetadata`` binds
timezone-aware UTC values. PostgreSQL converts an aware value to the *session*
time zone before dropping the offset, and every reader assumes the stored
wall-clock is UTC. On a server whose default ``timezone`` is not UTC each write
therefore landed ahead of real time by the zone's offset (enterprise#23).

These tests run the injector's real engines, the real drivers and
``SQLAppConversationInfoService`` against a database whose default time zone is
Europe/Berlin. The Cloud SQL connector itself is not available here; its two
creator paths run with a stand-in that opens plain driver connections and
forwards keyword arguments the way the connector's driver helpers do.
"""

from __future__ import annotations

import asyncio
import socket
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pg8000.dbapi
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from openhands.app_server.app_conversation.app_conversation_models import (
    AppConversationInfo,
)
from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    SQLAppConversationInfoService,
    StoredConversationMetadata,
)
from openhands.app_server.services import db_session_injector as injector_module
from openhands.app_server.services.db_session_injector import DbSessionInjector
from openhands.app_server.user.specifiy_user_context import SpecifyUserContext
from openhands.sdk import ConversationStats
from openhands.sdk.llm import Metrics, TokenUsage
from tests import postgres_testdb

# Europe/Berlin is UTC+1 in January and UTC+2 in July, so a session-zone
# conversion shows up as a different skew in each half of the year.
WINTER = datetime(2026, 1, 14, 10, 0, 0, tzinfo=UTC)
SUMMER = datetime(2026, 7, 14, 10, 0, 0, tzinfo=UTC)

STORED_TEXT_SQL = text(
    'SELECT created_at::text, last_updated_at::text FROM conversation_metadata '
    'WHERE conversation_id = :id'
)


@pytest.fixture
def berlin_database(
    test_database: postgres_testdb.TestDatabase,
    postgres_server: postgres_testdb.PostgresServer,
) -> postgres_testdb.TestDatabase:
    """This test's database with a non-UTC default, as a server GUC would give."""
    postgres_testdb._run_admin_sql(
        postgres_server,
        f'ALTER DATABASE {postgres_testdb._quote(test_database.name)} '
        "SET timezone = 'Europe/Berlin'",
    )
    return test_database


@pytest.fixture
async def make_injector(tmp_path):
    """Build injectors the way production does when ``DB_HOST`` is set."""
    injectors: list[DbSessionInjector] = []

    def _make(database: postgres_testdb.TestDatabase, **overrides) -> DbSessionInjector:
        injector = DbSessionInjector(
            persistence_dir=tmp_path,
            host=database.server.host,
            port=database.server.port,
            name=database.name,
            user=database.server.user,
            password=SecretStr(database.server.password),
            pool_size=1,
            max_overflow=0,
            **overrides,
        )
        injectors.append(injector)
        return injector

    yield _make
    for injector in injectors:
        await injector.close()
        if injector._engine is not None:
            injector._engine.dispose()


class _FakeConnector:
    """Stand-in for ``google.cloud.sql.connector.Connector`` on a plain server.

    The real connector's ``pg8000``/``asyncpg`` helpers pop ``user``, ``db`` and
    ``password`` and pass every other keyword argument to the driver's
    ``connect``; this does the same against the test server.
    """

    def __init__(self, database: postgres_testdb.TestDatabase, loop=None):
        self.database = database
        self._loop = loop
        self.calls: list[dict] = []

    def connect(self, instance: str, driver: str, **kwargs):
        assert driver == 'pg8000'
        self.calls.append(dict(kwargs))
        server = self.database.server
        return pg8000.dbapi.connect(
            kwargs.pop('user'),
            host=server.host,
            port=server.port,
            database=kwargs.pop('db'),
            password=kwargs.pop('password'),
            **kwargs,
        )

    async def connect_async(self, instance: str, driver: str, **kwargs):
        assert driver == 'asyncpg'
        self.calls.append(dict(kwargs))
        server = self.database.server
        return await asyncpg.connect(
            user=kwargs.pop('user'),
            database=kwargs.pop('db'),
            password=kwargs.pop('password'),
            host=server.host,
            port=server.port,
            **kwargs,
        )

    async def close_async(self):
        pass


def _info(conversation_id: UUID, at: datetime) -> AppConversationInfo:
    return AppConversationInfo(
        id=conversation_id,
        created_by_user_id=None,
        sandbox_id='sandbox',
        title='timestamps',
        created_at=at,
        updated_at=at,
    )


def _service(session) -> SQLAppConversationInfoService:
    return SQLAppConversationInfoService(
        db_session=session, user_context=SpecifyUserContext(user_id=None)
    )


async def _save(injector: DbSessionInjector, info: AppConversationInfo) -> None:
    maker = await injector.get_async_session_maker()
    async with maker() as session:
        await _service(session).save_app_conversation_info(info)


async def _load(injector: DbSessionInjector, conversation_id: UUID):
    maker = await injector.get_async_session_maker()
    async with maker() as session:
        info = await _service(session).get_app_conversation_info(conversation_id)
    assert info is not None
    return info


async def _stored_text(injector: DbSessionInjector, conversation_id) -> tuple:
    """The naive text PostgreSQL holds, which no session zone can dress up."""
    engine = await injector.get_async_db_engine()
    async with engine.connect() as conn:
        row = (await conn.execute(STORED_TEXT_SQL, {'id': str(conversation_id)})).one()
    return tuple(row)


def _utc_text(at: datetime) -> str:
    return at.strftime('%Y-%m-%d %H:%M:%S')


async def _show_timezone(async_engine) -> str:
    async with async_engine.connect() as conn:
        return (await conn.execute(text('SHOW TimeZone'))).scalar_one()


def _show_timezone_sync(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text('SHOW TimeZone')).scalar_one()


@pytest.mark.parametrize('at', [WINTER, SUMMER], ids=['winter', 'summer'])
async def test_service_round_trip_on_non_utc_database(
    berlin_database, make_injector, at
):
    """A conversation saved on one engine reads back unchanged on a fresh one."""
    conversation_id = uuid4()
    writer = make_injector(berlin_database)
    await _save(writer, _info(conversation_id, at))
    await writer.close()

    reader = make_injector(berlin_database)
    info = await _load(reader, conversation_id)

    assert info.created_at == at
    assert info.updated_at == at
    assert await _stored_text(reader, conversation_id) == (
        _utc_text(at),
        _utc_text(at),
    )


async def test_service_round_trip_on_utc_database(test_database, make_injector):
    """The UTC baseline keeps working as before."""
    conversation_id = uuid4()
    writer = make_injector(test_database)
    await _save(writer, _info(conversation_id, SUMMER))
    await writer.close()

    reader = make_injector(test_database)
    info = await _load(reader, conversation_id)

    assert info.created_at == SUMMER
    assert info.updated_at == SUMMER
    assert await _stored_text(reader, conversation_id) == (
        _utc_text(SUMMER),
        _utc_text(SUMMER),
    )


async def test_execution_status_update_writes_utc(berlin_database, make_injector):
    """``update_execution_status`` stamps ``last_updated_at`` with the real time."""
    conversation_id = uuid4()
    injector = make_injector(berlin_database)
    await _save(injector, _info(conversation_id, WINTER))

    maker = await injector.get_async_session_maker()
    before = datetime.now(UTC)
    async with maker() as session:
        await _service(session).update_execution_status(conversation_id, 'running')
    after = datetime.now(UTC)

    info = await _load(make_injector(berlin_database), conversation_id)
    assert info.created_at == WINTER
    assert (
        before - timedelta(seconds=1) <= info.updated_at <= after + timedelta(seconds=1)
    )


async def test_statistics_update_writes_utc(berlin_database, make_injector):
    """``update_conversation_statistics`` stamps ``last_updated_at`` likewise."""
    conversation_id = uuid4()
    injector = make_injector(berlin_database)
    await _save(injector, _info(conversation_id, WINTER))
    stats = ConversationStats(
        usage_to_metrics={
            'agent': Metrics(
                model_name='gpt-4',
                accumulated_cost=2.5,
                accumulated_token_usage=TokenUsage(
                    prompt_tokens=100, completion_tokens=50
                ),
            )
        }
    )

    maker = await injector.get_async_session_maker()
    before = datetime.now(UTC)
    async with maker() as session:
        await _service(session).update_conversation_statistics(conversation_id, stats)
    after = datetime.now(UTC)

    info = await _load(make_injector(berlin_database), conversation_id)
    assert info.created_at == WINTER
    assert (
        before - timedelta(seconds=1) <= info.updated_at <= after + timedelta(seconds=1)
    )


async def test_sync_engine_round_trip_on_non_utc_database(
    berlin_database, make_injector
):
    """The pg8000 engine behind ``session_maker`` stores UTC wall-clock too."""
    conversation_id = str(uuid4())
    writer = make_injector(berlin_database).get_db_engine()
    with Session(writer) as session:
        session.add(
            StoredConversationMetadata(
                conversation_id=conversation_id,
                sandbox_id='sandbox',
                conversation_version='V1',
                created_at=SUMMER,
                last_updated_at=SUMMER,
            )
        )
        session.commit()
    writer.dispose()

    reader = make_injector(berlin_database).get_db_engine()
    with Session(reader) as session:
        stored = session.get(StoredConversationMetadata, conversation_id)
        assert stored is not None
        assert stored.created_at.replace(tzinfo=UTC) == SUMMER
        assert stored.last_updated_at.replace(tzinfo=UTC) == SUMMER
        row = session.execute(STORED_TEXT_SQL, {'id': conversation_id}).one()
    assert tuple(row) == (_utc_text(SUMMER), _utc_text(SUMMER))


async def test_async_session_timezone_survives_pool_reuse(
    berlin_database, make_injector
):
    """The pin is a session default, so nothing the pool does can undo it.

    With one pooled connection every checkout below reuses the same session:
    after a read-only checkout (reset with a rollback), a failed transaction,
    repeated checkouts, an invalidated connection and a disposed pool.
    """
    engine = await make_injector(berlin_database).get_async_db_engine()

    assert await _show_timezone(engine) == 'UTC'
    assert await _show_timezone(engine) == 'UTC'
    with pytest.raises(ProgrammingError):
        async with engine.begin() as conn:
            await conn.execute(text('SELECT * FROM no_such_table'))
    assert await _show_timezone(engine) == 'UTC'
    for _ in range(3):
        assert await _show_timezone(engine) == 'UTC'
    async with engine.connect() as conn:
        await conn.invalidate()
    assert await _show_timezone(engine) == 'UTC'
    await engine.dispose()
    assert await _show_timezone(engine) == 'UTC'


async def test_sync_session_timezone_survives_pool_reuse(
    berlin_database, make_injector
):
    """Same as above for the pg8000 engine."""
    engine = make_injector(berlin_database).get_db_engine()

    assert _show_timezone_sync(engine) == 'UTC'
    assert _show_timezone_sync(engine) == 'UTC'
    with pytest.raises(ProgrammingError):
        with engine.begin() as conn:
            conn.execute(text('SELECT * FROM no_such_table'))
    assert _show_timezone_sync(engine) == 'UTC'
    for _ in range(3):
        assert _show_timezone_sync(engine) == 'UTC'
    with engine.connect() as conn:
        conn.invalidate()
    assert _show_timezone_sync(engine) == 'UTC'
    engine.dispose()
    assert _show_timezone_sync(engine) == 'UTC'


async def test_existing_rows_are_left_as_stored(berlin_database, make_injector):
    """Only new writes change; a row already on disk keeps its text."""
    injector = make_injector(berlin_database)
    engine = await injector.get_async_db_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                'INSERT INTO conversation_metadata (conversation_id, sandbox_id, '
                'conversation_version, created_at, last_updated_at) VALUES '
                "(:id, 'sandbox', 'V1', '2026-01-14 11:00:00', '2026-01-14 11:00:00')"
            ),
            {'id': 'legacy'},
        )

    await _save(injector, _info(uuid4(), WINTER))

    assert await _stored_text(injector, 'legacy') == (
        '2026-01-14 11:00:00',
        '2026-01-14 11:00:00',
    )


async def test_cloud_sql_sync_creator_pins_utc(berlin_database, make_injector):
    """``_create_gcp_engine`` asks the connector for a UTC pg8000 session."""
    injector = make_injector(
        berlin_database,
        gcp_db_instance='instance',
        gcp_project='project',
        gcp_region='region',
    )
    connector = _FakeConnector(berlin_database)
    injector._gcp_connector = connector

    engine = injector.get_db_engine()
    assert _show_timezone_sync(engine) == 'UTC'
    assert _show_timezone_sync(engine) == 'UTC'
    assert connector.calls == [
        {
            'user': berlin_database.server.user,
            'password': berlin_database.server.password,
            'db': berlin_database.name,
            'startup_params': {'timezone': 'UTC'},
        }
    ]


async def test_cloud_sql_async_creator_pins_utc(
    berlin_database, make_injector, monkeypatch
):
    """``_create_async_gcp_engine`` asks the connector for a UTC asyncpg session."""
    # ``test_db_session_injector`` imports the module with a stubbed ``asyncpg``;
    # the adapter built here must wrap the real driver.
    monkeypatch.setattr(injector_module, 'asyncpg', asyncpg)
    injector = make_injector(
        berlin_database,
        gcp_db_instance='instance',
        gcp_project='project',
        gcp_region='region',
    )
    connector = _FakeConnector(berlin_database, loop=asyncio.get_running_loop())
    injector._gcp_connector = connector

    engine = await injector.get_async_db_engine()
    assert await _show_timezone(engine) == 'UTC'
    conversation_id = uuid4()
    await _save(injector, _info(conversation_id, SUMMER))
    assert await _stored_text(injector, conversation_id) == (
        _utc_text(SUMMER),
        _utc_text(SUMMER),
    )
    assert connector.calls == [
        {
            'user': berlin_database.server.user,
            'password': berlin_database.server.password,
            'db': berlin_database.name,
            'server_settings': {'timezone': 'UTC'},
        }
    ]


async def test_cloud_sql_pg8000_helper_forwards_startup_params(berlin_database):
    """The installed connector hands ``startup_params`` to pg8000 unchanged.

    Its pg8000 helper takes an already-open socket; a plain one to the test
    server stands in for the connector's TLS socket.
    """
    from google.cloud.sql.connector import pg8000 as connector_pg8000

    server = berlin_database.server
    sock = socket.create_connection((server.host, server.port))
    conn = connector_pg8000.connect(
        server.host,
        sock,  # type: ignore[arg-type]
        user=server.user,
        password=server.password,
        db=berlin_database.name,
        startup_params={'timezone': 'UTC'},
    )
    try:
        cursor = conn.cursor()
        cursor.execute('SHOW TimeZone')
        assert cursor.fetchone()[0] == 'UTC'
    finally:
        conn.close()
