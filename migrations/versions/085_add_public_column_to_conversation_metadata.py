"""add public column to conversation_metadata

Revision ID: 085
Revises: 084
Create Date: 2025-01-27 00:00:00.000000

"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '085'
down_revision: str | None = '084'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'conversation_metadata',
        sa.Column('public', sa.Boolean(), nullable=True),
    )
    op.create_index(
        op.f('ix_conversation_metadata_public'),
        'conversation_metadata',
        ['public'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_conversation_metadata_public'),
        table_name='conversation_metadata',
    )
    op.drop_column('conversation_metadata', 'public')
