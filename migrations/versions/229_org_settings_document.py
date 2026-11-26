"""Replace organization preference columns with a single JSON document."""

import sqlalchemy as sa
from alembic import op

revision = '229'
down_revision = '224'
branch_labels = None
depends_on = None

_COLUMNS = {
    'meta_profiles': (sa.JSON(), None),
    'agent_settings': (sa.JSON(), None),
    'conversation_settings': (sa.JSON(), None),
    'registered_marketplaces': (sa.JSON(), None),
    'remote_runtime_resource_factor': (sa.Integer(), None),
    'enable_proactive_conversation_starters': (sa.Boolean(), 'true'),
    'sandbox_base_container_image': (sa.String(), None),
    'sandbox_runtime_container_image': (sa.String(), None),
    'max_budget_per_task': (sa.Float(), None),
    'v1_enabled': (sa.Boolean(), None),
    'conversation_expiration': (sa.Integer(), None),
    'enable_conversation_usage_detail': (sa.Boolean(), 'false'),
    'enable_shared_conversation_discovery': (sa.Boolean(), 'true'),
    'sandbox_grouping_strategy': (sa.String(), None),
    'default_sandbox_spec_name': (sa.String(), None),
}


def upgrade() -> None:
    # Both upstream 224 changes are prerequisites. Do not silently cut over a
    # database stamped on only one side of the conflicting revision history.
    inspector = sa.inspect(op.get_bind())
    if not any(
        c['name'] == 'sandbox_image' for c in inspector.get_columns('v1_remote_sandbox')
    ):
        raise RuntimeError(
            'Resolve the upstream 223/224 migration collision before settings cutover'
        )
    op.add_column(
        'org', sa.Column('settings', sa.JSON(), nullable=False, server_default='{}')
    )
    pairs = ', '.join(f"'{name}', {name}" for name in _COLUMNS)
    op.execute(sa.text(f'UPDATE org SET settings = json_build_object({pairs})'))
    for name in _COLUMNS:
        op.drop_column('org', name)


def downgrade() -> None:
    for name, (kind, _) in _COLUMNS.items():
        op.add_column('org', sa.Column(name, kind))
    record = ', '.join(
        f'{name} {kind.compile(dialect=op.get_bind().dialect)}'
        for name, (kind, _) in _COLUMNS.items()
    )
    values = ', '.join(f'{name} = legacy.{name}' for name in _COLUMNS)
    op.execute(
        sa.text(
            f'UPDATE org SET {values} FROM '
            f'(SELECT source.id, prefs.* FROM org source CROSS JOIN LATERAL '
            f'json_to_record(source.settings) AS prefs({record})) legacy '
            f'WHERE org.id = legacy.id'
        )
    )
    for name, (_, default) in _COLUMNS.items():
        if default:
            op.execute(
                sa.text(f'UPDATE org SET {name} = {default} WHERE {name} IS NULL')
            )
            op.alter_column(
                'org', name, nullable=False, server_default=sa.text(default)
            )
    for name in ('agent_settings', 'conversation_settings'):
        op.execute(
            sa.text(f"UPDATE org SET {name} = '{{}}'::json WHERE {name} IS NULL")
        )
        op.alter_column('org', name, nullable=False)
    op.drop_column('org', 'settings')
