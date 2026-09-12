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
    # PostgreSQL's fast-default path records 0 for existing rows without a
    # full-table UPDATE. Changing the default afterward makes only new rows
    # start current while legacy rows remain distinguishable and retryable.
    op.add_column(
        'org_member',
        sa.Column(
            'managed_llm_key_ownership_version',
            sa.Integer(),
            nullable=False,
            server_default=sa.text('0'),
        ),
    )
    op.alter_column(
        'org_member',
        'managed_llm_key_ownership_version',
        server_default=sa.text('1'),
    )


def downgrade() -> None:
    op.drop_column('org_member', 'managed_llm_key_ownership_version')
