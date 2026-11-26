"""Add tenant-safe team settings scopes.

Revision ID: 227
Revises: 226
Create Date: 2026-11-22 00:00:00.000000

A team is identified by the explicit ``(org_id, team_id)`` pair and owns its
settings document plus a revision directly on the ``team`` row; there is no
second settings table. Membership is a separate row whose composite foreign
keys require the team and the user to belong to the same organization, so a
settings write can never cross tenants by passing a team id it does not own.

Rollback drops only the team tables; the org/member document migration (226)
rolls back independently.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '227'
down_revision: str | None = '226'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'team',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column(
            'org_id',
            sa.Uuid(),
            sa.ForeignKey('org.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        # The single settings document owned by this team scope.
        sa.Column(
            'settings',
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            'revision', sa.Integer(), nullable=False, server_default=sa.text('0')
        ),
        sa.Column(
            'updated_by',
            sa.Uuid(),
            sa.ForeignKey('user.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column(
            'created_by',
            sa.Uuid(),
            sa.ForeignKey('user.id', ondelete='SET NULL'),
            nullable=True,
        ),
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
        sa.UniqueConstraint('org_id', 'name', name='uq_team_org_id_name'),
        # Target of the composite (org_id, team_id) foreign key below.
        sa.UniqueConstraint('org_id', 'id', name='uq_team_org_id_id'),
    )
    op.create_index('ix_team_org_id', 'team', ['org_id'])

    op.create_table(
        'team_member',
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('team_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('org_id', 'team_id', 'user_id', name='pk_team_member'),
        sa.ForeignKeyConstraint(
            ['org_id', 'team_id'],
            ['team.org_id', 'team.id'],
            name='fk_team_member_team',
            ondelete='CASCADE',
        ),
        # The member must already belong to the same tenant. This is the
        # database-level half of "safe explicit tenant/membership identity".
        sa.ForeignKeyConstraint(
            ['org_id', 'user_id'],
            ['org_member.org_id', 'org_member.user_id'],
            name='fk_team_member_org_member',
            ondelete='CASCADE',
        ),
    )
    op.create_index('ix_team_member_org_user', 'team_member', ['org_id', 'user_id'])


def downgrade() -> None:
    op.drop_index('ix_team_member_org_user', table_name='team_member')
    op.drop_table('team_member')
    op.drop_index('ix_team_org_id', table_name='team')
    op.drop_table('team')
