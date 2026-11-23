"""Add the license a Super Admin saves to instance_settings.

Revision ID: 214
Revises: 213
Create Date: 2026-11-10
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '214'
down_revision: str | None = '213'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'instance_settings',
        sa.Column('license_key', sa.String(), nullable=True),
    )
    op.add_column(
        'instance_settings',
        sa.Column('offline_license', sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('instance_settings', 'offline_license')
    op.drop_column('instance_settings', 'license_key')
