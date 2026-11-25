"""Add the member's LLM profile selection diff."""

import sqlalchemy as sa
from alembic import op

revision = '222'
down_revision = '221'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('org_member', sa.Column('llm_selection_diff', sa.JSON()))


def downgrade() -> None:
    op.drop_column('org_member', 'llm_selection_diff')
