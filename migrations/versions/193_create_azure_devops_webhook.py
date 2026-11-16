"""create azure devops webhook table

Revision ID: 193
Revises: 192
Create Date: 2026-10-21 00:00:00.000000

Records the org-wide Azure DevOps resolver Service Hooks per organization, so
members who can't view Service Hooks in Azure DevOps can still see that, and
when, they were installed.
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '193'
down_revision: str | None = '192'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'azure_devops_webhook',
        sa.Column(
            'id', sa.Integer(), nullable=False, primary_key=True, autoincrement=True
        ),
        sa.Column('organization', sa.String(), nullable=False),
        sa.Column('installed_by_user_id', sa.String(), nullable=True),
        sa.Column('installed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('pr_subscription_id', sa.String(), nullable=True),
        sa.Column('work_item_subscription_id', sa.String(), nullable=True),
    )
    op.create_unique_constraint(
        'uq_azure_devops_webhook_organization',
        'azure_devops_webhook',
        ['organization'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_azure_devops_webhook_organization',
        'azure_devops_webhook',
        type_='unique',
    )
    op.drop_table('azure_devops_webhook')
