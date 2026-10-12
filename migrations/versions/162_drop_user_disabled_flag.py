"""Drop the reverted user disabled flag when present.

Revision ID: 162
Revises: 161
Create Date: 2026-09-12 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '162'
down_revision: str | None = '161'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = sa.inspect(bind).get_columns('user')
    if any(column['name'] == 'is_disabled' for column in columns):
        op.drop_column('user', 'is_disabled')


def downgrade() -> None:
    pass
