"""Upgrade legacy budget settings without inferring ownership from their caps."""

import subprocess
import sys
from importlib import import_module
from unittest.mock import Mock

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from scripts import check_enterprise_migration_roundtrip as roundtrip
from storage.llm_credential_operation import LlmCredentialOperation
from storage.org_budget_operation import OrgBudgetOperation
from storage.org_budget_settings import OrgBudgetSettings

migration = import_module('migrations.versions.164_add_budget_control_operations')
baseline_migration = import_module(
    'migrations.versions.162_add_org_budget_cycle_baseline'
)


def test_upgrade_preserves_legacy_policy_and_requires_explicit_adoption(
    engine, create_org
):
    enabled = create_org().id
    disabled = create_org().id
    with engine.begin() as connection:
        connection.execute(text('DROP TABLE llm_credential_operation'))
        connection.execute(text('DROP FUNCTION protect_llm_credential_intent()'))
        connection.execute(text('DROP TABLE org_budget_operation'))
        connection.execute(text('DROP FUNCTION protect_budget_operation_intent()'))
        for column in (
            'cycle_end_at',
            'cycle_allowance',
            'cycle_default_user_allowance',
            'cycle_user_allowances',
            'control_mode',
            'control_generation',
            'control_changed_at',
            'control_changed_by',
        ):
            connection.execute(
                text(f'ALTER TABLE org_budget_settings DROP COLUMN {column}')
            )
        for org_id, is_enabled in ((enabled, True), (disabled, False)):
            connection.execute(
                text("""
                INSERT INTO org_budget_settings (
                    org_id, enabled, monthly_limit, reset_day, cycle_start_at,
                    cycle_start_spend, user_cycle_start_spend,
                    litellm_last_member_spend, litellm_known_member_ids, created_at, updated_at
                ) VALUES (
                    :org_id, :enabled, 1, 1, CURRENT_TIMESTAMP, 302.55,
                    '{}', '{}', '[]', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            """),
                {'org_id': org_id, 'enabled': is_enabled},
            )
        before = (
            connection.execute(text('SELECT * FROM org_budget_settings'))
            .mappings()
            .all()
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        after = (
            connection.execute(text('SELECT * FROM org_budget_settings'))
            .mappings()
            .all()
        )
        old = {row['org_id']: dict(row) for row in before}
        for row in after:
            assert {field: row[field] for field in old[row['org_id']]} == old[
                row['org_id']
            ]
            assert row['control_mode'] == (
                'needs_adoption' if row['enabled'] else 'external'
            )
            assert row['control_generation'] == 0
            assert row['control_changed_by'] is None
        assert connection.scalar(text('SELECT count(*) FROM org_budget_operation')) == 0
        assert (
            connection.scalar(text('SELECT count(*) FROM llm_credential_operation'))
            == 0
        )


def test_online_downgrade_cannot_remove_write_ownership():
    with pytest.raises(RuntimeError, match='ownership-aware rollback'):
        migration.downgrade()


@pytest.mark.parametrize('driver', ['pg8000', ''])
def test_actual_alembic_downgrade_preserves_ownership_and_evidence(
    test_database, engine, session_maker, create_org, monkeypatch, driver
):
    server = test_database.server
    for name, value in {
        'DB_HOST': server.host,
        'DB_PORT': str(server.port),
        'DB_USER': server.user,
        'DB_PASS': server.password,
        'DB_NAME': test_database.name,
        'DB_DRIVER': driver,
        'DB_SSL_MODE': 'disable',
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('GCP_DB_INSTANCE', raising=False)
    org = create_org()
    with session_maker() as session:
        session.add(
            LlmCredentialOperation(
                org_id=org.id,
                user_id='test-user',
                request_hash='b' * 64,
                key_hash='c' * 64,
                payload={'key': 'sk-pending-candidate'},
                status='pending',
            )
        )
        session.add(
            OrgBudgetSettings(
                org_id=org.id,
                enabled=True,
                control_mode='managed',
                control_generation=2,
                cycle_start_spend=302.55,
            )
        )
        for generation, status in ((1, 'applied'), (2, 'pending')):
            session.add(
                OrgBudgetOperation(
                    org_id=org.id,
                    idempotency_key=f'operation-{generation}',
                    request_hash='a' * 64,
                    generation=generation,
                    kind='adopt',
                    actor='test-admin',
                    plan={'team_baseline': 302.55},
                    status=status,
                    verification={'team_spend': 302.55}
                    if status == 'applied'
                    else None,
                )
            )
        session.commit()

    def snapshot():
        with engine.connect() as connection:
            return {
                table: connection.execute(
                    text(f'SELECT to_jsonb(t) FROM {table} t ORDER BY id')
                )
                .scalars()
                .all()
                for table in (
                    'org_budget_settings',
                    'org_budget_operation',
                    'llm_credential_operation',
                )
            }

    before = snapshot()
    subprocess.run(
        [sys.executable, '-m', 'scripts.check_enterprise_migration_roundtrip'],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert snapshot() == before
    with engine.connect() as connection:
        assert (
            connection.scalar(text('SELECT version_num FROM alembic_version')) == '164'
        )
    with pytest.raises(DBAPIError, match='verification evidence is immutable'):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE org_budget_operation SET verification = '{}' WHERE status = 'applied'"
                )
            )
    assert snapshot() == before


@pytest.mark.parametrize('failure', [None, RuntimeError('unrelated database failure')])
def test_ci_cannot_accept_missing_fence_or_unrelated_downgrade_failure(
    monkeypatch, failure
):
    monkeypatch.setattr(roundtrip, 'current_heads', lambda config: ('164',))
    monkeypatch.setattr(roundtrip.command, 'downgrade', Mock(side_effect=failure))
    upgrade = Mock()
    monkeypatch.setattr(roundtrip.command, 'upgrade', upgrade)
    with pytest.raises(RuntimeError if failure else AssertionError):
        roundtrip.check_roundtrip(Config('alembic.ini'))
    upgrade.assert_not_called()


def test_future_migration_still_requires_normal_roundtrip(monkeypatch):
    script = Mock()
    script.get_current_head.return_value = '165'
    script.get_revision.return_value.down_revision = '164'
    monkeypatch.setattr(
        roundtrip.ScriptDirectory, 'from_config', Mock(return_value=script)
    )
    monkeypatch.setattr(
        roundtrip, 'current_heads', Mock(side_effect=[('165',), ('164',), ('165',)])
    )
    downgrade, upgrade = Mock(), Mock()
    monkeypatch.setattr(roundtrip.command, 'downgrade', downgrade)
    monkeypatch.setattr(roundtrip.command, 'upgrade', upgrade)
    config = Config('alembic.ini')
    roundtrip.check_roundtrip(config)
    downgrade.assert_called_once_with(config, '-1')
    upgrade.assert_called_once_with(config, 'head')


@pytest.mark.parametrize(
    'legacy',
    [
        '[]',
        'null',
        '"not a map"',
        '123',
        'true',
        '{"valid":12.5,"zero":0,"negative":-1,"text":"20","null":null,"bool":true,"list":[],"huge":1e9999,"tiny":1e-9999}',
    ],
)
def test_baseline_import_preserves_invalid_legacy_data_without_inventing_zero(
    engine, session_maker, create_org, legacy
):
    org = create_org()
    with session_maker() as session:
        session.add(OrgBudgetSettings(org_id=org.id, enabled=True))
        session.commit()
    with engine.begin() as connection:
        connection.execute(text('DROP TABLE org_budget_cycle_baseline'))
        connection.execute(
            text(
                'UPDATE org_budget_settings SET user_cycle_start_spend = CAST(:legacy AS json)'
            ),
            {'legacy': legacy},
        )
        with Operations.context(MigrationContext.configure(connection)):
            baseline_migration.upgrade()
        assert (
            connection.scalar(
                text('SELECT user_cycle_start_spend::text FROM org_budget_settings')
            )
            == legacy
        )
        imported = dict(
            connection.execute(
                text('SELECT user_id, baseline_spend FROM org_budget_cycle_baseline')
            ).all()
        )
        assert imported == (
            {'valid': 12.5, 'zero': 0.0} if legacy.startswith('{') else {}
        )
