"""Real-DB tests for migration 203 (the ``service_account`` role row)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from storage.org import Org
from storage.org_member import OrgMember
from storage.user import User
from tests import postgres_testdb


def _service_account_role(engine: Engine) -> tuple[int, int] | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, rank FROM role WHERE name = 'service_account'")
        ).one_or_none()
    return None if row is None else (row.id, row.rank)


def _upgrade_from_202(test_database: postgres_testdb.TestDatabase) -> None:
    # The test template truncates ``role``, so re-run 203 to seed the row.
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '202'
    )
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', '203'
    )


def test_upgrade_inserts_service_account_role_after_member(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _upgrade_from_202(test_database)

    role = _service_account_role(engine)
    assert role is not None
    assert role[1] == 2000


def test_downgrade_removes_unused_service_account_role(
    engine: Engine, test_database: postgres_testdb.TestDatabase
):
    _upgrade_from_202(test_database)

    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'downgrade', '202'
    )

    assert _service_account_role(engine) is None


def test_downgrade_refuses_while_a_member_has_the_role(
    engine: Engine,
    session_maker: sessionmaker,
    test_database: postgres_testdb.TestDatabase,
):
    _upgrade_from_202(test_database)
    # The ORM models match head, so seed rows there; downgrading to 202
    # passes through the later migrations first.
    postgres_testdb.run_alembic(
        test_database.server, test_database.name, 'upgrade', 'head'
    )
    role = _service_account_role(engine)
    assert role is not None
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    with session_maker() as session:
        session.add(Org(id=org_id, name='acme'))
        session.flush()
        session.add(User(id=user_id, current_org_id=org_id))
        session.flush()
        session.add(
            OrgMember(
                org_id=org_id,
                user_id=user_id,
                role_id=role[0],
                status='active',
                llm_api_key='test-key',
            )
        )
        session.commit()

    with pytest.raises(
        postgres_testdb.PostgresUnavailableError, match='service_account'
    ):
        postgres_testdb.run_alembic(
            test_database.server, test_database.name, 'downgrade', '202'
        )

    assert _service_account_role(engine) == role
