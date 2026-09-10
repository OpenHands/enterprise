"""Track managed LiteLLM key ownership reconciliation.

Revision ID: 161
Revises: 160
Create Date: 2026-09-10 00:00:00.000000

"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '161'
down_revision: str | None = '160'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keep existing rows distinguishable from rows created by fixed code. The
    # maintenance runner advances a legacy row only after ownership is verified,
    # repaired, or proven irrelevant (BYOK/non-managed).
    op.add_column(
        'org_member',
        sa.Column('managed_llm_key_ownership_version', sa.Integer(), nullable=True),
    )
    op.execute(sa.text('UPDATE org_member SET managed_llm_key_ownership_version = 0'))
    op.alter_column(
        'org_member',
        'managed_llm_key_ownership_version',
        nullable=False,
        server_default=sa.text('1'),
    )


def downgrade() -> None:
    op.drop_column('org_member', 'managed_llm_key_ownership_version')
