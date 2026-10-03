"""Enforce single active Jira user link.

Revision ID: 147
Revises: 146
Create Date: 2026-08-15 00:00:00.000000

"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '147'
down_revision: str | None = '146'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = 'uq_jira_users_one_active_per_user'


def upgrade() -> None:
    # Keep the newest active link per user before adding the uniqueness guard.
    op.execute(
        sa.text(
            """
            UPDATE jira_users
            SET status = 'inactive'
            WHERE status = 'active'
              AND id NOT IN (
                SELECT id
                FROM (
                  SELECT
                    id,
                    ROW_NUMBER() OVER (
                      PARTITION BY keycloak_user_id
                      ORDER BY created_at DESC, id DESC
                    ) AS row_num
                  FROM jira_users
                  WHERE status = 'active'
                ) ranked
                WHERE row_num = 1
              )
            """
        )
    )

    op.create_index(
        INDEX_NAME,
        'jira_users',
        ['keycloak_user_id'],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name='jira_users')
