"""Normalize legacy empty tool settings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '141'
down_revision = '140'
branch_labels = None
depends_on = None


def _empty_tools_update(
    table_name: str,
    column_name: str,
) -> sa.Update:
    table = sa.table(table_name, sa.column(column_name, sa.JSON()))
    column = table.c[column_name]
    document = sa.cast(column, postgresql.JSONB())
    empty_array = sa.cast(sa.literal('[]'), postgresql.JSONB())
    normalized = sa.cast(
        sa.func.jsonb_set(
            document,
            sa.cast(sa.literal('{tools}'), postgresql.ARRAY(sa.Text())),
            sa.cast(sa.literal('null'), postgresql.JSONB()),
        ),
        sa.JSON(),
    )
    return (
        table.update()
        .where(document['tools'] == empty_array)
        .values({column_name: normalized})
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    for table_name, column_name in (
        ('user_settings', 'agent_settings'),
        ('org', 'agent_settings'),
        ('org_member', 'agent_settings_diff'),
    ):
        bind.execute(_empty_tools_update(table_name, column_name))


def downgrade() -> None:
    pass
