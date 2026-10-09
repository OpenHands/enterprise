"""Restore lifecycle state without changing the historical 157/162 migrations."""

import sqlalchemy as sa
from alembic import op

revision = '184'
down_revision = '183'
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
