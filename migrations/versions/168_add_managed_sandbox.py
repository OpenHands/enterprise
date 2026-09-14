"""Add durable inventory for app-managed Docker sandboxes."""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '168'
down_revision: str | None = '167'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'v1_managed_sandbox',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('created_by_user_id', sa.String(), nullable=False),
        sa.Column('sandbox_spec_id', sa.String(), nullable=False),
        sa.Column('application_id', sa.String(), nullable=False),
        sa.Column('resource_id', sa.String(), nullable=False, unique=True),
        sa.Column('container_name', sa.String(), nullable=False, unique=True),
        sa.Column('container_id', sa.String(), nullable=True),
        sa.Column('initializer_name', sa.String(), nullable=False, unique=True),
        sa.Column('owned_volume_names', sa.JSON(), nullable=False),
        sa.Column('launch_spec_ciphertext', sa.Text(), nullable=False),
        sa.Column('session_key_ciphertext', sa.Text(), nullable=False),
        sa.Column('workspace_key_ciphertext', sa.Text(), nullable=False),
        sa.Column('session_api_key_hash', sa.String(), nullable=True),
        sa.Column('desired_state', sa.String(), nullable=False),
        sa.Column('initialized_generation', sa.String(), nullable=True),
        sa.Column('initializing_generation', sa.String(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    for column in (
        'created_by_user_id',
        'sandbox_spec_id',
        'session_api_key_hash',
        'desired_state',
        'created_at',
    ):
        op.create_index(
            f'ix_v1_managed_sandbox_{column}', 'v1_managed_sandbox', [column]
        )


def downgrade() -> None:
    op.drop_table('v1_managed_sandbox')
