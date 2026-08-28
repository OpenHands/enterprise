"""Soft-delete conversations and conversations start tasks.

Adds a nullable ``deleted_at`` marker to ``conversation_metadata`` and
``app_conversation_start_task``. Deleting a conversation now sets ``deleted_at``
(instead of hard-deleting the row) so the data/audit trail is retained for
reconciliation with telemetry and abuse auditing, while every user-facing read
path filters ``deleted_at`` out so the API behaves as if the conversation were
gone.

This mirrors the OSS app-lifespan soft-delete concept; the enterprise deployment
maintains its own migration chain and therefore needs this parallel migration.

Revision ID: 154
Revises: 153
Create Date: 2026-08-13 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '154'
down_revision: Union[str, None] = '153'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_deleted_at(table: str, index_name: str) -> None:
    op.add_column(
        table,
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        index_name,
        table,
        ['deleted_at'],
        unique=False,
        postgresql_where=sa.text('deleted_at IS NOT NULL'),
    )


def _drop_deleted_at(table: str, index_name: str) -> None:
    op.drop_index(index_name, table_name=table)
    op.drop_column(table, 'deleted_at')


def upgrade() -> None:
    """Upgrade schema: add soft-delete markers."""
    _add_deleted_at('conversation_metadata', 'ix_conversation_metadata_deleted_at')
    _add_deleted_at(
        'app_conversation_start_task',
        'ix_app_conversation_start_task_deleted_at',
    )


def downgrade() -> None:
    """Downgrade schema: drop soft-delete markers."""
    _drop_deleted_at(
        'app_conversation_start_task', 'ix_app_conversation_start_task_deleted_at'
    )
    _drop_deleted_at('conversation_metadata', 'ix_conversation_metadata_deleted_at')
