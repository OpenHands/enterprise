"""Scope temporary grants by owner.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-24
"""

from __future__ import annotations

from alembic import op


revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE temporary_grants ADD COLUMN IF NOT EXISTS owner_id TEXT")
    op.execute(
        """
        UPDATE temporary_grants AS temporary_grant
        SET owner_id = COALESCE(request.notification_targets->>'in_app', 'legacy:null')
        FROM access_requests AS request
        WHERE temporary_grant.owner_id IS NULL
          AND temporary_grant.agent_id IS NOT DISTINCT FROM request.agent_id
          AND temporary_grant.agent_class IS NOT DISTINCT FROM request.agent_class
          AND temporary_grant.integration_key = request.integration_key
          AND temporary_grant.function_name = request.function_name
        """
    )
    op.execute(
        "UPDATE temporary_grants SET owner_id = 'legacy:null' WHERE owner_id IS NULL"
    )
    op.execute("ALTER TABLE temporary_grants ALTER COLUMN owner_id SET NOT NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS temporary_grants_owner_id_idx ON temporary_grants (LOWER(owner_id))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS temporary_grants_owner_id_idx")
    op.execute("ALTER TABLE temporary_grants DROP COLUMN IF EXISTS owner_id")
