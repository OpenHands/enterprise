"""Add the ``service_account`` role.

Service accounts are non-human org members. Their role ranks after
``member`` (owner 10, admin 20, member 1000). Downgrade refuses while any
org member, user or invitation still references the role.

Revision ID: 207
Revises: 206
Create Date: 2026-10-26 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '207'
down_revision: str | None = '206'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO role (name, rank) VALUES ('service_account', 2000) "
            'ON CONFLICT (name) DO NOTHING'
        )
    )


def downgrade() -> None:
    in_use = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT
                    EXISTS (SELECT 1 FROM org_member WHERE role_id = r.id)
                    OR EXISTS (SELECT 1 FROM "user" WHERE role_id = r.id)
                    OR EXISTS (SELECT 1 FROM org_invitation WHERE role_id = r.id)
                FROM role r
                WHERE r.name = 'service_account'
                """
            )
        )
        .scalar()
    )
    if in_use:
        raise RuntimeError(
            "Cannot downgrade: the 'service_account' role is still in use. "
            'Remove its org members first.'
        )
    op.execute(sa.text("DELETE FROM role WHERE name = 'service_account'"))
