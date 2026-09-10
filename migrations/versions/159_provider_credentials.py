"""Store manual Git credentials and verified provider account associations.

Revision ID: 159
Revises: 158
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '159'
down_revision: str | None = '158'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'auth_tokens',
        sa.Column(
            'credential_kind', sa.String(), nullable=False, server_default='oauth'
        ),
    )
    op.add_column(
        'auth_tokens', sa.Column('provider_account_id', sa.String(), nullable=True)
    )
    op.add_column('auth_tokens', sa.Column('provider_host', sa.String(), nullable=True))
    for name, column_type in (
        ('refresh_token', sa.String()),
        ('access_token_expires_at', sa.BigInteger()),
        ('refresh_token_expires_at', sa.BigInteger()),
    ):
        op.alter_column('auth_tokens', name, existing_type=column_type, nullable=True)
    op.create_check_constraint(
        'ck_auth_tokens_kind', 'auth_tokens', "credential_kind IN ('manual', 'oauth')"
    )
    op.create_check_constraint(
        'ck_auth_tokens_account_host',
        'auth_tokens',
        'provider_account_id IS NULL OR provider_host IS NOT NULL',
    )
    op.create_check_constraint(
        'ck_auth_tokens_manual_credential',
        'auth_tokens',
        "credential_kind != 'manual' OR (provider_account_id IS NOT NULL AND provider_host IS NOT NULL AND refresh_token IS NULL AND access_token_expires_at IS NULL AND refresh_token_expires_at IS NULL)",
    )
    # Legacy broker rows deliberately remain unassociated until provider control
    # has been verified. NULL IDs do not collide in this unique index.
    op.create_index(
        'idx_auth_tokens_provider_account',
        'auth_tokens',
        ['identity_provider', 'provider_host', 'provider_account_id'],
        unique=True,
    )


def downgrade() -> None:
    # Manual credentials cannot be represented safely by the old refresh schema.
    # Require an explicit cleanup instead of silently deleting users' credentials.
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT 1 FROM auth_tokens WHERE credential_kind = 'manual' LIMIT 1")
    ).first():
        raise RuntimeError('Disconnect manual provider credentials before downgrading')
    op.drop_index('idx_auth_tokens_provider_account', table_name='auth_tokens')
    op.drop_constraint('ck_auth_tokens_account_host', 'auth_tokens', type_='check')
    op.drop_constraint('ck_auth_tokens_manual_credential', 'auth_tokens', type_='check')
    op.drop_constraint('ck_auth_tokens_kind', 'auth_tokens', type_='check')
    for name, column_type in (
        ('refresh_token', sa.String()),
        ('access_token_expires_at', sa.BigInteger()),
        ('refresh_token_expires_at', sa.BigInteger()),
    ):
        op.alter_column('auth_tokens', name, existing_type=column_type, nullable=False)
    op.drop_column('auth_tokens', 'provider_host')
    op.drop_column('auth_tokens', 'provider_account_id')
    op.drop_column('auth_tokens', 'credential_kind')
