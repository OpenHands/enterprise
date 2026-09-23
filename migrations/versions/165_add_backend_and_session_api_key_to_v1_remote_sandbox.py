"""Add backend and session_api_key to v1_remote_sandbox.

This lets every sandbox backend record its sandboxes in the table. ``backend``
says which one owns a row. Existing rows all belong to the remote backend, and
app servers still on the previous release insert rows without a backend while
a deploy rolls out, so the column defaults to ``remote``.

``session_api_key`` holds the key itself, encrypted by ``StoredSecretStr``, for
backends that cannot read a key back from the provider.

Revision ID: 165
Revises: 164
Create Date: 2026-09-22 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '165'
down_revision: str | None = '164'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'v1_remote_sandbox',
        sa.Column('backend', sa.String(), nullable=False, server_default='remote'),
    )
    op.add_column(
        'v1_remote_sandbox',
        sa.Column('session_api_key', sa.String(), nullable=True),
    )


def downgrade() -> None:
    # The previous release reads every row as a remote sandbox.
    op.execute("DELETE FROM v1_remote_sandbox WHERE backend <> 'remote'")
    op.drop_column('v1_remote_sandbox', 'session_api_key')
    op.drop_column('v1_remote_sandbox', 'backend')
