"""Pin the working directory of new Runtime API sandboxes."""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '167'
down_revision: str | None = '166'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'v1_remote_sandbox', sa.Column('working_dir', sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('v1_remote_sandbox', 'working_dir')
