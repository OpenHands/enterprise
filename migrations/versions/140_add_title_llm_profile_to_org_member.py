"""Add title LLM profile preference to organization members."""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '140'
down_revision: str | None = '139'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'org_member',
        sa.Column('title_llm_profile', sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('org_member', 'title_llm_profile')
