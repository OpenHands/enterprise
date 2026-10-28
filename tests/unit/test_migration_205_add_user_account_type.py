"""Real-DB tests for migration 205 (``user.account_type`` and ``display_name``)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

from storage.org import Org
from tests import postgres_testdb


def _insert_org(session_maker: sessionmaker) -> uuid.UUID:
    org_id = uuid.uuid4()
    with session_maker() as session:
        session.add(Org(id=org_id, name=f'org_{org_id}'))
        session.commit()
    return org_id


def _insert_user(engine: Engine, org_id: uuid.UUID, **columns: object) -> uuid.UUID:
    user_id = uuid.uuid4()
    names = ', '.join(['id', 'current_org_id', *columns])
    values = ', '.join([':id', ':current_org_id', *(f':{name}' for name in columns)])
    with engine.begin() as conn:
        conn.execute(
            text(f'INSERT INTO "user" ({names}) VALUES ({values})'),
            {'id': user_id, 'current_org_id': org_id, **columns},
        )
    return user_id


def _user_columns(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(
            conn.execute(
                text(
                    'SELECT column_name FROM information_schema.columns '
                    "WHERE table_name = 'user'"
                )
            ).scalars()
        )


def test_upgrade_reads_existing_users_as_human(
    engine: Engine,
    session_maker: sessionmaker,
    test_database: postgres_testdb.TestDatabase,
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '204'
    )
    org_id = _insert_org(session_maker)
    user_id = _insert_user(engine, org_id)

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '205'
    )

    with engine.connect() as conn:
        row = conn.execute(
            text('SELECT account_type, display_name FROM "user" WHERE id = :id'),
            {'id': user_id},
        ).one()
    assert row.account_type == 'human'
    assert row.display_name is None


def test_upgrade_rejects_unknown_account_type(
    engine: Engine, session_maker: sessionmaker
):
    org_id = _insert_org(session_maker)

    # pg8000 reports a CHECK violation as ProgrammingError, not IntegrityError.
    with pytest.raises(DBAPIError, match='ck_user_account_type'):
        _insert_user(engine, org_id, account_type='robot')


def test_service_account_names_are_unique_per_org_ignoring_case(
    engine: Engine, session_maker: sessionmaker
):
    org_id = _insert_org(session_maker)
    other_org_id = _insert_org(session_maker)
    _insert_user(engine, org_id, account_type='service', display_name='CI bot')
    # The same name in another org, or on a human, is allowed.
    _insert_user(engine, other_org_id, account_type='service', display_name='CI bot')
    _insert_user(engine, org_id, account_type='human', display_name='CI bot')

    with pytest.raises(IntegrityError, match='uq_user_service_account_name'):
        _insert_user(engine, org_id, account_type='service', display_name='ci BOT')


def test_downgrade_drops_columns(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '205'
    )
    assert {'account_type', 'display_name'} <= _user_columns(engine)

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '204'
    )

    columns = _user_columns(engine)
    assert 'account_type' not in columns
    assert 'display_name' not in columns


def test_downgrade_refuses_while_service_accounts_exist(
    engine: Engine,
    session_maker: sessionmaker,
    test_database: postgres_testdb.TestDatabase,
):
    org_id = _insert_org(session_maker)
    _insert_user(engine, org_id, account_type='service', display_name='CI bot')

    with pytest.raises(postgres_testdb.PostgresUnavailableError, match='service'):
        postgres_testdb.run_alembic(
            test_database.server, test_database.name, 'downgrade', '204'
        )

    assert 'account_type' in _user_columns(engine)
