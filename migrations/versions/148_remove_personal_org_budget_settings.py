"""Remove budget settings accidentally created for personal workspaces.

Revision ID: 148
Revises: 147
Create Date: 2026-06-16 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '148'
down_revision: str | None = '147'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    user = sa.table('user', sa.column('id', sa.Uuid()))
    personal_org_ids = sa.select(user.c.id)

    for table_name in (
        'org_user_budget_override',
        'org_budget_threshold',
        'org_budget_settings',
    ):
        table = sa.table(table_name, sa.column('org_id', sa.Uuid()))
        op.execute(table.delete().where(table.c.org_id.in_(personal_org_ids)))


def downgrade() -> None:
    pass
