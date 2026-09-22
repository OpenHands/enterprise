"""Add the v1_sandbox table shared by the docker and E2B sandbox backends.

Revision ID: 165
Revises: 164
Create Date: 2026-09-22 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '165'
down_revision: str | None = '164'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the app-owned sandbox record the docker and E2B backends share.

    Those two backends stored ownership in container labels and E2B sandbox
    metadata, which is neither durable nor mutable enough to back an
    authorization decision. ``v1_remote_sandbox`` is untouched.
    """
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    op.create_table(
        'v1_sandbox',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('backend', sa.String(), nullable=False),
        sa.Column('created_by_user_id', sa.String(), nullable=True),
        sa.Column('sandbox_spec_id', sa.String(), nullable=False),
        sa.Column('session_api_key_hash', sa.String(), nullable=True),
        # The key itself, encrypted at rest. Only the E2B backend writes it:
        # docker reads the key back off the container and the runtime API
        # returns it on every read, so neither needs it kept.
        sa.Column('session_api_key', sa.String(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    for column in (
        'backend',
        'created_by_user_id',
        'sandbox_spec_id',
        'session_api_key_hash',
        'created_at',
        'deleted_at',
    ):
        op.create_index(
            op.f(f'ix_v1_sandbox_{column}'),
            'v1_sandbox',
            [column],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table('v1_sandbox')
