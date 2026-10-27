"""Add ``account_type`` and ``display_name`` to ``user``.

``account_type`` tells people (``human``) from service accounts
(``service``). Existing rows are people. A service account is a non-human
member of one org: it has no personal org, no email and no password, and
``display_name`` holds its name. Service account names are unique per org,
ignoring case. Downgrade refuses while a service account exists.

Revision ID: 203
Revises: 202
Create Date: 2026-10-26 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '203'
down_revision: str | None = '202'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'user',
        sa.Column(
            'account_type',
            sa.String(),
            nullable=False,
            server_default=sa.text("'human'"),
        ),
    )
    op.add_column('user', sa.Column('display_name', sa.String(), nullable=True))
    op.create_check_constraint(
        'ck_user_account_type', 'user', "account_type IN ('human', 'service')"
    )
    # A service account never changes org, so ``current_org_id`` is its org.
    op.create_index(
        'uq_user_service_account_name',
        'user',
        ['current_org_id', sa.text('lower(display_name)')],
        unique=True,
        postgresql_where=sa.text("account_type = 'service'"),
    )


def downgrade() -> None:
    has_service_accounts = (
        op.get_bind()
        .execute(
            sa.text(
                """SELECT EXISTS (SELECT 1 FROM "user" WHERE account_type = 'service')"""
            )
        )
        .scalar()
    )
    if has_service_accounts:
        raise RuntimeError(
            'Cannot downgrade: service accounts exist. Delete them first.'
        )
    op.drop_index('uq_user_service_account_name', table_name='user')
    op.drop_constraint('ck_user_account_type', 'user', type_='check')
    op.drop_column('user', 'display_name')
    op.drop_column('user', 'account_type')
