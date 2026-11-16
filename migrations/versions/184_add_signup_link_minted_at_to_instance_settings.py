"""Record on instance_settings when a Super Admin last minted a sign-up link.

Revision ID: 184
Revises: 183
Create Date: 2026-10-11
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '184'
down_revision: str | None = '183'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'instance_settings',
        sa.Column('signup_link_minted_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('instance_settings', 'signup_link_minted_at')
