from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '143_add_member_agent_settings.py'
)
spec = spec_from_file_location('migration_143', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_143 = module_from_spec(spec)
spec.loader.exec_module(migration_143)


def test_upgrade_and_downgrade_preserve_legacy_member_settings(monkeypatch):
    engine = sa.create_engine('sqlite://')
    metadata = sa.MetaData()
    org_member = sa.Table(
        'org_member',
        metadata,
        sa.Column('org_id', sa.String(), primary_key=True),
        sa.Column('user_id', sa.String(), primary_key=True),
        sa.Column('agent_settings_diff', sa.JSON(), nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            org_member.insert().values(
                org_id='org',
                user_id='user',
                agent_settings_diff={'llm': {'model': 'legacy-model'}},
            )
        )
        context = MigrationContext.configure(connection)
        monkeypatch.setattr(migration_143, 'op', Operations(context))

        migration_143.upgrade()

        columns = {
            column['name']
            for column in sa.inspect(connection).get_columns('org_member')
        }
        assert 'agent_settings' in columns
        upgraded = connection.execute(
            sa.text(
                'SELECT agent_settings_diff, agent_settings '
                'FROM org_member WHERE org_id = :org_id'
            ),
            {'org_id': 'org'},
        ).one()
        assert upgraded.agent_settings is None
        assert 'legacy-model' in upgraded.agent_settings_diff

        migration_143.downgrade()

        columns = {
            column['name']
            for column in sa.inspect(connection).get_columns('org_member')
        }
        assert 'agent_settings' not in columns
        downgraded = connection.execute(
            sa.text(
                'SELECT agent_settings_diff FROM org_member WHERE org_id = :org_id'
            ),
            {'org_id': 'org'},
        ).scalar_one()
        assert 'legacy-model' in downgraded
