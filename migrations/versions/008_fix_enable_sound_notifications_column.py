"""fix enable_sound_notifications settings to not be nullable

Revision ID: 008
Revises: 007
Create Date: 2025-02-28 18:28:00.000

"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '008'
down_revision: str | None = '007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        'settings',
        sa.Column(
            'enable_sound_notifications', sa.Boolean(), nullable=False, default=False
        ),
    )


def downgrade() -> None:
    op.alter_column(
        'settings',
        sa.Column(
            'enable_sound_notifications', sa.Boolean(), nullable=True, default=False
        ),
    )
