"""Add organization configuration policy and member LLM profiles.

Revision ID: 189
Revises: 188
Create Date: 2026-10-14 16:30:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '189'
down_revision: str | None = '188'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_POLICY = '{"llm":{"operation":"extend"}}'


def upgrade() -> None:
    op.add_column(
        'org',
        sa.Column(
            'configuration_policy',
            sa.JSON(),
            nullable=False,
            server_default=sa.text(f"'{_DEFAULT_POLICY}'::json"),
        ),
    )
    op.add_column(
        'org_member',
        sa.Column('llm_profiles', sa.String(), nullable=True),
    )
    op.add_column(
        'org_member',
        sa.Column('active_llm_profile_name', sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('org_member', 'active_llm_profile_name')
    op.drop_column('org_member', 'llm_profiles')
    op.drop_column('org', 'configuration_policy')
