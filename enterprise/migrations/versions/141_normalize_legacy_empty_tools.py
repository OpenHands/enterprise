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
    dialect_name: str,
) -> sa.Update:
    table = sa.table(table_name, sa.column(column_name, sa.JSON()))
    column = table.c[column_name]

    if dialect_name == 'postgresql':
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

    if dialect_name == 'sqlite':
        return (
            table.update()
            .where(sa.func.json_type(column, '$.tools') == 'array')
            .where(
                sa.func.json_array_length(sa.func.json_extract(column, '$.tools')) == 0
            )
            .values(
                {
                    column_name: sa.func.json_set(
                        column,
                        '$.tools',
                        sa.func.json('null'),
                    )
                }
            )
        )

    raise RuntimeError(f'Unsupported database dialect: {dialect_name}')


def upgrade() -> None:
    bind = op.get_bind()
    for table_name, column_name in (
        ('user_settings', 'agent_settings'),
        ('org', 'agent_settings'),
        ('org_member', 'agent_settings_diff'),
    ):
        bind.execute(
            _empty_tools_update(
                table_name,
                column_name,
                bind.dialect.name,
            )
        )


def downgrade() -> None:
    pass
