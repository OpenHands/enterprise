"""Soft-delete conversations and conversations start tasks.

Adds a nullable ``deleted_at`` marker to ``conversation_metadata`` and
``app_conversation_start_task``. Deleting a conversation now sets ``deleted_at``
(instead of hard-deleting the row) so the data/audit trail is retained for
reconciliation with telemetry and abuse auditing, while every user-facing read
path filters ``deleted_at`` out so the API behaves as if the conversation were
gone.

This mirrors the OSS app-lifespan soft-delete concept; the enterprise deployment
maintains its own migration chain and therefore needs this parallel migration.

Revision ID: 144
Revises: 143
Create Date: 2026-08-13 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '144'
down_revision: Union[str, None] = '143'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_deleted_at(table: str, index_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column['name'] for column in inspector.get_columns(table)}
    indexes = {index['name'] for index in inspector.get_indexes(table)}

    if 'deleted_at' not in columns:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(
                sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True)
            )

    if index_name not in indexes:
        op.create_index(index_name, table, ['deleted_at'], unique=False)


def _drop_deleted_at(table: str, index_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column['name'] for column in inspector.get_columns(table)}
    indexes = {index['name'] for index in inspector.get_indexes(table)}

    if index_name in indexes:
        op.drop_index(index_name, table_name=table)

    if 'deleted_at' in columns:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column('deleted_at')


def upgrade() -> None:
    """Upgrade schema: add soft-delete markers."""
    _add_deleted_at(
        'conversation_metadata', 'ix_conversation_metadata_deleted_at'
    )
    _add_deleted_at(
        'app_conversation_start_task', 'ix_app_conversation_start_task_deleted_at'
    )


def downgrade() -> None:
    """Downgrade schema: drop soft-delete markers."""
    _drop_deleted_at(
        'app_conversation_start_task', 'ix_app_conversation_start_task_deleted_at'
    )
    _drop_deleted_at(
        'conversation_metadata', 'ix_conversation_metadata_deleted_at'
    )