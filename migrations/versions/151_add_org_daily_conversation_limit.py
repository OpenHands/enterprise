"""Add org-level daily conversation limit override."""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '151'
down_revision: str | None = '150'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    op.add_column(
        'org',
        sa.Column('daily_conversation_limit', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('org', 'daily_conversation_limit')
