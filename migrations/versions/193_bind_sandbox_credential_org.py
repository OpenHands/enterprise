"""Persist the organization that authorizes sandbox credential lookups.

Revision ID: 193
Revises: 192

Leave legacy rows unbound: the owner's current org is not evidence of origin.
No foreign key so deleting an org cannot erase or retarget this identity.
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '193'
down_revision: str | None = '192'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'v1_remote_sandbox', sa.Column('credential_org_id', sa.Uuid(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('v1_remote_sandbox', 'credential_org_id')
