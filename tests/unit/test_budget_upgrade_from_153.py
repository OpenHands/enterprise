"""Reconstruct the legacy four-org/95-member shape, without customer identities."""

import subprocess
import sys
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import MetaData, Table, create_engine, select, text
from sqlalchemy.pool import NullPool

from storage.org import Org
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_member import OrgMember
from storage.user import User
from tests import postgres_testdb


@pytest.mark.parametrize('driver', ['pg8000', ''])
def test_full_upgrade_from_153_preserves_legacy_policy_without_adopting(
    postgres_server, monkeypatch, driver
):
    database = postgres_testdb.create_test_database(postgres_server, 'template0')
    engine = create_engine(
        postgres_server.url(database, driver=driver), poolclass=NullPool
    )
    for name, value in {
        'DB_HOST': postgres_server.host,
        'DB_PORT': str(postgres_server.port),
        'DB_USER': postgres_server.user,
        'DB_PASS': postgres_server.password,
        'DB_NAME': database,
        'DB_DRIVER': driver,
        'DB_SSL_MODE': 'disable',
        'WEB_HOST': '',
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('GCP_DB_INSTANCE', raising=False)
    monkeypatch.delenv('STRIPE_API_KEY', raising=False)
    try:

        def migrate(revision):
            subprocess.run(
                [sys.executable, '-m', 'alembic', 'upgrade', revision],
                check=True,
                capture_output=True,
                text=True,
                timeout=90,
            )

        migrate('153')
        metadata = MetaData()

        def insert(connection, model, **values):
            table = Table(
                model.__tablename__,
                metadata,
                autoload_with=connection,
                extend_existing=True,
            )
            defaults = {
                column.name: (
                    column.default.arg(None)
                    if column.default.is_callable
                    else column.default.arg
                )
                for column in model.__table__.columns
                if column.name in table.c and column.default is not None
            }
            connection.execute(table.insert().values(**(defaults | values)))

        roster = {}
        with engine.begin() as connection:
            role_id = connection.scalar(
                text("SELECT id FROM role WHERE name = 'owner'")
            )
            assert role_id is not None
            for index, (count, default) in enumerate(
                ((9, 1000), (51, 100), (16, 1000), (19, 1000))
            ):
                org_id = uuid4()
                insert(connection, Org, id=org_id, name=f'legacy-budget-{index}')
                insert(
                    connection,
                    OrgBudgetSettings,
                    org_id=org_id,
                    enabled=True,
                    monthly_limit=1,
                    default_user_monthly_limit=default,
                    cycle_start_at=datetime(2026, 8, 1, tzinfo=UTC),
                    cycle_start_spend=0,
                    user_cycle_start_spend={},
                    litellm_last_sync_status='success',
                )
                roster[org_id] = []
                for _ in range(count):
                    user_id = uuid4()
                    roster[org_id].append(str(user_id))
                    insert(connection, User, id=user_id, current_org_id=org_id)
                    insert(
                        connection,
                        OrgMember,
                        org_id=org_id,
                        user_id=user_id,
                        role_id=role_id,
                        _llm_api_key='synthetic-test-key',
                    )
            before = {
                row['org_id']: dict(row)
                for row in connection.execute(
                    text('SELECT * FROM org_budget_settings')
                ).mappings()
            }

        migrate('head')
        with engine.connect() as connection:
            assert (
                connection.scalar(text('SELECT version_num FROM alembic_version'))
                == '164'
            )
            rows = (
                connection.execute(select(OrgBudgetSettings.__table__)).mappings().all()
            )
            assert len(rows) == 4
            for row in rows:
                assert {key: row[key] for key in before[row['org_id']]} == before[
                    row['org_id']
                ]
                assert row['control_mode'] == 'needs_adoption'
                assert row['control_generation'] == 0
                assert row['cycle_allowance'] is None
                assert sorted(row['litellm_known_member_ids']) == sorted(
                    roster[row['org_id']]
                )
            assert connection.scalar(text('SELECT count(*) FROM org_member')) == 95
            assert (
                connection.scalar(
                    text('SELECT count(*) FROM org_budget_cycle_baseline')
                )
                == 0
            )
            assert (
                connection.scalar(text('SELECT count(*) FROM org_budget_operation'))
                == 0
            )
            assert (
                connection.scalar(
                    text(
                        'SELECT count(*) FROM org_member WHERE managed_llm_key_ownership_version = 0'
                    )
                )
                == 95
            )
    finally:
        engine.dispose()
        postgres_testdb.drop_test_database(postgres_server, database)
