"""add mcp_config to user_settings

Revision ID: 036
Revises: 035
Create Date: 2025-05-08

"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '036'
down_revision: str | None = '035'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('user_settings', sa.Column('mcp_config', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('user_settings', 'mcp_config')
