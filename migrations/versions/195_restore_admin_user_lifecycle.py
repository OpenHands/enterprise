"""Restore retryable administrator lifecycle state."""

import sqlalchemy as sa
from alembic import op

revision = '195'
down_revision = '194'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'user',
        sa.Column(
            'deletion_pending', sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        'user',
        sa.Column('credentials_revoked_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column('user', 'credentials_revoked_at')
    op.drop_column('user', 'deletion_pending')
