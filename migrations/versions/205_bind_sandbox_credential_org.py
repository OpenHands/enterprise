"""Persist the organization that authorizes sandbox credential lookups.

Revision ID: 205
Revises: 204

Existing sandboxes are bound from their own conversation records: only when
every conversation the sandbox owner recorded on it names the same org. Any
other existing sandbox stays unbound. The owner's current org is never used.
No foreign key so deleting an org cannot erase or retarget this identity.
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '205'
down_revision: str | None = '204'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'v1_remote_sandbox', sa.Column('credential_org_id', sa.Uuid(), nullable=True)
    )
    op.execute(
        """
        UPDATE v1_remote_sandbox AS sandbox
        SET credential_org_id = recorded.org_id
        FROM (
            SELECT metadata.sandbox_id, (array_agg(saas.org_id))[1] AS org_id
            FROM conversation_metadata AS metadata
            JOIN conversation_metadata_saas AS saas
                ON saas.conversation_id = metadata.conversation_id
            JOIN v1_remote_sandbox AS owned
                ON owned.id = metadata.sandbox_id
                AND owned.created_by_user_id = saas.user_id::text
            WHERE metadata.sandbox_id IS NOT NULL
            GROUP BY metadata.sandbox_id
            HAVING COUNT(DISTINCT saas.org_id) = 1
        ) AS recorded
        WHERE sandbox.id = recorded.sandbox_id
            AND sandbox.credential_org_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column('v1_remote_sandbox', 'credential_org_id')
