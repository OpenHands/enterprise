"""Add first-install wizard and setup-guide state to instance_settings.

Revision ID: 177
Revises: 176
Create Date: 2026-10-04
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '177'
down_revision: str | None = '176'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'instance_settings', sa.Column('setup_user_id', sa.UUID(), nullable=True)
    )
    op.add_column(
        'instance_settings',
        sa.Column(
            'wizard_completed',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        'instance_settings', sa.Column('guide_org_id', sa.UUID(), nullable=True)
    )
    op.add_column(
        'instance_settings',
        sa.Column(
            'guide_dismissed',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_foreign_key(
        'fk_instance_settings_guide_org_id',
        'instance_settings',
        'org',
        ['guide_org_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_instance_settings_guide_org_id',
        'instance_settings',
        type_='foreignkey',
    )
    op.drop_column('instance_settings', 'guide_dismissed')
    op.drop_column('instance_settings', 'guide_org_id')
    op.drop_column('instance_settings', 'wizard_completed')
    op.drop_column('instance_settings', 'setup_user_id')
