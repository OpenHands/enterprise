"""Add private sandbox specs, org grants for them, and an org default spec.

Sandbox specs without a row in sandbox_spec_access stay public, so this changes
nothing until a super admin marks a spec private.

Revision ID: 225
Revises: 224
Create Date: 2026-11-25
"""

from importlib import import_module
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '225'
down_revision: str | None = '224'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    access = inspector.has_table('sandbox_spec_access')
    grant = inspector.has_table('sandbox_spec_org_grant')
    default = any(
        c['name'] == 'default_sandbox_spec_name' for c in inspector.get_columns('org')
    )
    if access and grant and default:
        # The unreleased sandbox lineage used the same stamps as Bitbucket.
        # Its schema proves those stamps may have skipped the Bitbucket work.
        for module in (
            '223_update_bitbucket_scopes',
            '224_seed_bitbucket_dc_login_idp',
        ):
            import_module(f'migrations.versions.{module}').upgrade()
        return
    if access or grant or default:
        raise RuntimeError('Incomplete sandbox spec access schema at revision 224')
    op.create_table(
        'sandbox_spec_access',
        sa.Column('sandbox_name', sa.String(), primary_key=True),
        sa.Column('is_private', sa.Boolean(), nullable=False),
    )
    op.create_table(
        'sandbox_spec_org_grant',
        sa.Column(
            'sandbox_name',
            sa.String(),
            sa.ForeignKey('sandbox_spec_access.sandbox_name', ondelete='CASCADE'),
            primary_key=True,
        ),
        sa.Column(
            'org_id',
            sa.UUID(),
            sa.ForeignKey('org.id', ondelete='CASCADE'),
            primary_key=True,
        ),
    )
    op.create_index(
        'ix_sandbox_spec_org_grant_org_id', 'sandbox_spec_org_grant', ['org_id']
    )
    op.add_column(
        'org', sa.Column('default_sandbox_spec_name', sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('org', 'default_sandbox_spec_name')
    op.drop_index('ix_sandbox_spec_org_grant_org_id', 'sandbox_spec_org_grant')
    op.drop_table('sandbox_spec_org_grant')
    op.drop_table('sandbox_spec_access')
