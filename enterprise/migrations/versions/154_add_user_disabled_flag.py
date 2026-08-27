"""Add an instance-level disabled flag to users.

Revision ID: 154
Revises: 143
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '154'
down_revision: Union[str, None] = '153'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'user',
        sa.Column(
            'is_disabled', sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.alter_column('user', 'is_disabled', server_default=None)


def downgrade() -> None:
    op.drop_column('user', 'is_disabled')
