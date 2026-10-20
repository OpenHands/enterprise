"""Add opaque revisions and latest-replacement receipts to custom secrets.

Revision ID: 194
Revises: 192

Existing values and ciphertext are unchanged. No value hashes or history are
stored. Personal rows receive an unused revision for compatible shared storage.
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '194'
down_revision: str | None = '192'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'custom_secrets',
        sa.Column(
            'value_version',
            sa.Uuid(),
            nullable=False,
            server_default=sa.text('gen_random_uuid()'),
        ),
    )
    op.add_column(
        'custom_secrets', sa.Column('last_replacement_id', sa.Uuid(), nullable=True)
    )
    op.add_column(
        'custom_secrets',
        sa.Column('last_replacement_expected_version', sa.Uuid(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('custom_secrets', 'last_replacement_expected_version')
    op.drop_column('custom_secrets', 'last_replacement_id')
    op.drop_column('custom_secrets', 'value_version')
