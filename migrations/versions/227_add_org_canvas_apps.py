"""Add the Agent Canvas Apps approved for an organization and who enabled them.

Nothing reads these tables until a super admin turns Agent Canvas Apps on and
approves an app, so this changes nothing for existing installs.

Revision ID: 227
Revises: 226
Create Date: 2026-11-27
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '227'
down_revision: str | None = '226'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'org_canvas_app',
        sa.Column(
            'org_id',
            sa.UUID(),
            sa.ForeignKey('org.id', ondelete='CASCADE'),
            primary_key=True,
        ),
        sa.Column('name', sa.String(), primary_key=True),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('requested_ref', sa.String(), nullable=True),
        sa.Column('resolved_ref', sa.String(), nullable=False),
        sa.Column('repo_path', sa.String(), nullable=True),
        sa.Column('manifest', sa.JSON(), nullable=False),
        sa.Column('bundle', sa.LargeBinary(), nullable=False),
        sa.Column('installed_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        'org_member_canvas_app',
        sa.Column('org_id', sa.UUID(), primary_key=True),
        sa.Column('user_id', sa.UUID(), primary_key=True),
        sa.Column('name', sa.String(), primary_key=True),
        sa.ForeignKeyConstraint(
            ['org_id', 'name'],
            ['org_canvas_app.org_id', 'org_canvas_app.name'],
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['org_id', 'user_id'],
            ['org_member.org_id', 'org_member.user_id'],
            ondelete='CASCADE',
        ),
    )


def downgrade() -> None:
    op.drop_table('org_member_canvas_app')
    op.drop_table('org_canvas_app')
