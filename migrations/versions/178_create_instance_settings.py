"""Create instance_settings for the Super Admin company name and logo.

Revision ID: 178
Revises: 177
Create Date: 2026-10-04
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '178'
down_revision: str | None = '177'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'instance_settings',
        sa.Column(
            'id',
            sa.Integer(),
            nullable=False,
            primary_key=True,
            server_default='1',
        ),
        sa.Column('company_name', sa.String(255), nullable=True),
        sa.Column('logo', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text('CURRENT_TIMESTAMP'),
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text('CURRENT_TIMESTAMP'),
        ),
    )
    op.create_check_constraint(
        'single_instance_settings_row', 'instance_settings', 'id = 1'
    )


def downgrade() -> None:
    op.drop_table('instance_settings')
