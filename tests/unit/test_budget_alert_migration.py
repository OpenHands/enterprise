from datetime import UTC, datetime
from importlib import import_module

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def test_alert_delivery_migration_preserves_existing_completed_threshold(
    engine, create_org
):
    org = create_org()
    migration = import_module('migrations.versions.166_track_budget_alert_delivery')
    cycle = datetime(2026, 9, 1, tzinfo=UTC)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            connection.execute(
                text("""
                    INSERT INTO org_budget_threshold
                        (org_id, percentage, email_enabled, slack_enabled,
                         last_triggered_cycle_start, last_triggered_at, created_at, updated_at)
                    VALUES (:org, 80, true, true, :cycle, :cycle, now(), now())
                """),
                {'org': org.id, 'cycle': cycle},
            )
            migration.upgrade()
            row = (
                connection.execute(text('SELECT * FROM org_budget_threshold'))
                .mappings()
                .one()
            )
            assert row['delivery_state'] == {}
            assert row['last_triggered_cycle_start'] == cycle
            assert row['last_triggered_at'] == cycle
            migration.downgrade()
            assert (
                connection.execute(
                    text('SELECT last_triggered_cycle_start FROM org_budget_threshold')
                ).scalar_one()
                == cycle
            )
