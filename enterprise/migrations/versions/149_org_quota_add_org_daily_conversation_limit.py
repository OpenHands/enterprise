"""Add org-level daily conversation limit override."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '149_org_quota'
down_revision: Union[str, None] = '148'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
