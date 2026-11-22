"""Add the SCIM users-only prototype tables.

Additive only: ``scim_credential`` (one organization per dedicated token) and
``scim_user`` (organization-scoped directory records and their deactivation
markers). The routes are off unless ``SCIM_ENABLED`` is set.

The downgrade retains both tables. ``scim_user`` rows with ``deactivated_at`` set
are what stop a deactivated person being added back to an organization, so
dropping them on a rollback would silently lift that block.

Revision ID: 220
Revises: 219
Create Date: 2026-11-22
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '220'
down_revision: str | None = '219'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREDENTIAL = 'scim_credential'
_USER = 'scim_user'


def upgrade() -> None:
    # Checked per table so a re-upgrade after the retaining downgrade, or after
    # a run that stopped between the two, creates only what is missing.
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if _CREDENTIAL not in existing:
        op.create_table(
            _CREDENTIAL,
            sa.Column('id', sa.Uuid(), primary_key=True),
            sa.Column(
                'org_id',
                sa.Uuid(),
                sa.ForeignKey('org.id', ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column(
                'auth_provider_id',
                sa.Integer(),
                sa.ForeignKey('auth_providers.id', ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
            sa.Column(
                'created_at',
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index('ix_scim_credential_org_id', _CREDENTIAL, ['org_id'])
    if _USER not in existing:
        op.create_table(
            _USER,
            sa.Column('id', sa.Uuid(), primary_key=True),
            sa.Column(
                'org_id',
                sa.Uuid(),
                sa.ForeignKey('org.id', ondelete='CASCADE'),
                nullable=False,
            ),
            sa.Column(
                'user_id',
                sa.Uuid(),
                sa.ForeignKey('user.id', ondelete='SET NULL'),
                nullable=True,
            ),
            sa.Column('user_name', sa.String(255), nullable=False),
            sa.Column('external_id', sa.String(255), nullable=True),
            sa.Column('email', sa.String(255), nullable=False),
            sa.Column('given_name', sa.String(255), nullable=True),
            sa.Column('family_name', sa.String(255), nullable=True),
            sa.Column('display_name', sa.String(255), nullable=True),
            sa.Column('active', sa.Boolean(), nullable=False),
            sa.Column('deactivated_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('membership_origin', sa.String(16), nullable=True),
            sa.Column(
                'created_at',
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                'updated_at',
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            'uq_scim_user_org_user_name',
            _USER,
            ['org_id', sa.text('lower(user_name)')],
            unique=True,
        )
        op.create_index(
            'uq_scim_user_org_external_id',
            _USER,
            ['org_id', 'external_id'],
            unique=True,
            postgresql_where=sa.text('external_id IS NOT NULL'),
        )
        op.create_index(
            'uq_scim_user_org_user_id',
            _USER,
            ['org_id', 'user_id'],
            unique=True,
            postgresql_where=sa.text('user_id IS NOT NULL'),
        )
        op.create_index(
            'uq_scim_user_org_email', _USER, ['org_id', 'email'], unique=True
        )


def downgrade() -> None:
    # Non-destructive: see the module docstring. Rollback is the feature flag.
    pass
