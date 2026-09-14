"""add accepted_tos to user_settings

Revision ID: 029
Revises: 028
Create Date: 2025-04-23

"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '029'
down_revision: str | None = '028'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'user_settings', sa.Column('accepted_tos', sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('user_settings', 'accepted_tos')
