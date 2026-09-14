"""Add is_org_shared flag to custom_secrets.

Distinguishes per-user (personal) secrets from org-wide shared secrets.
Existing rows are personal by default.

Revision ID: 162
Revises: 161
Create Date: 2026-06-05 00:00:00.000000

"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '163'
down_revision: str | None = '162'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'custom_secrets',
        sa.Column(
            'is_org_shared',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )


def downgrade() -> None:
    op.drop_column('custom_secrets', 'is_org_shared')
