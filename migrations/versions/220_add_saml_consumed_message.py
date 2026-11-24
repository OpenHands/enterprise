"""Add shared native SAML replay protection.

Revision ID: 220
Revises: 219
Create Date: 2026-11-20
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '220'
down_revision: str | None = '219'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'saml_consumed_message',
        sa.Column('id', sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column(
            'provider_id',
            sa.Integer(),
            sa.ForeignKey('auth_providers.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('message_kind', sa.String(), nullable=False),
        sa.Column('message_id', sa.String(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            'provider_id', 'message_kind', 'message_id', name='uq_saml_consumed_message'
        ),
    )
    op.create_index(
        'ix_saml_consumed_message_expires_at', 'saml_consumed_message', ['expires_at']
    )


def downgrade() -> None:
    op.drop_index(
        'ix_saml_consumed_message_expires_at', table_name='saml_consumed_message'
    )
    op.drop_table('saml_consumed_message')
