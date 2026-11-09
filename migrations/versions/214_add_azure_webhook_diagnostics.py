"""Retain sanitized Azure webhook install and verification evidence.

Revision ID: 214
Revises: 213
"""

import sqlalchemy as sa
from alembic import op

revision = '214'
down_revision = '213'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'azure_devops_webhook_diagnostics',
        sa.Column('organization', sa.String(), primary_key=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_install_attempt', sa.JSON(), nullable=True),
        sa.Column('last_observation', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('azure_devops_webhook_diagnostics')
