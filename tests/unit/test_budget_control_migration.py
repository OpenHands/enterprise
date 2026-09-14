"""Upgrade legacy budget settings without inferring ownership from their caps."""

from importlib import import_module

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

migration = import_module('migrations.versions.163_add_budget_control_operations')


def test_upgrade_preserves_legacy_policy_and_requires_explicit_adoption(
    engine, create_org
):
    enabled = create_org().id
    disabled = create_org().id
    with engine.begin() as connection:
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


def test_online_downgrade_cannot_remove_write_ownership():
    with pytest.raises(RuntimeError, match='ownership-aware rollback'):
        migration.downgrade()
