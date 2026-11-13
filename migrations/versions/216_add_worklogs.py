"""Add worklogs and their entries.

A worklog is a named, append-only log scoped to a factory. Factories live in
the automation service's database, so ``worklogs.factory_id`` is a plain UUID
with no foreign key. Entries are never updated or deleted by the API.

Revision ID: 216
Revises: 215
Create Date: 2026-11-13
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '216'
down_revision: str | None = '215'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'worklogs',
        sa.Column('id', sa.UUID(), primary_key=True),
        sa.Column('org_id', sa.UUID(), nullable=False),
        sa.Column('factory_id', sa.UUID(), nullable=False),
        sa.Column('slug', sa.String(63), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(['org_id'], ['org.id'], ondelete='CASCADE'),
        sa.UniqueConstraint(
            'org_id', 'factory_id', 'slug', name='uq_worklogs_factory_slug'
        ),
        sa.UniqueConstraint(
            'org_id', 'factory_id', 'name', name='uq_worklogs_factory_name'
        ),
    )
    op.create_index(
        'uq_worklogs_one_default_per_factory',
        'worklogs',
        ['org_id', 'factory_id'],
        unique=True,
        postgresql_where=sa.text('is_default'),
    )
    op.create_index('ix_worklogs_factory_id', 'worklogs', ['factory_id'])

    op.create_table(
        'worklog_entries',
        sa.Column('id', sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column('worklog_id', sa.UUID(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('content_type', sa.String(16), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('author_kind', sa.String(16), nullable=False),
        sa.Column('author_id', sa.String(255), nullable=True),
        sa.Column('author_name', sa.String(256), nullable=True),
        sa.Column('run_id', sa.UUID(), nullable=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['worklog_id'], ['worklogs.id'], ondelete='CASCADE'),
        sa.CheckConstraint(
            "content_type IN ('markdown', 'json')",
            name='ck_worklog_entries_content_type',
        ),
        sa.CheckConstraint(
            "author_kind IN ('user', 'automation')",
            name='ck_worklog_entries_author_kind',
        ),
    )
    op.create_index(
        'ix_worklog_entries_worklog_created',
        'worklog_entries',
        ['worklog_id', 'created_at', 'id'],
    )
    op.create_index(
        'ix_worklog_entries_user_author',
        'worklog_entries',
        ['author_id'],
        postgresql_where=sa.text("author_kind = 'user'"),
    )


def downgrade() -> None:
    op.drop_index('ix_worklog_entries_user_author', table_name='worklog_entries')
    op.drop_index('ix_worklog_entries_worklog_created', table_name='worklog_entries')
    op.drop_table('worklog_entries')
    op.drop_index('ix_worklogs_factory_id', table_name='worklogs')
    op.drop_index('uq_worklogs_one_default_per_factory', table_name='worklogs')
    op.drop_table('worklogs')
