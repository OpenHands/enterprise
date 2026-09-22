"""Drop legacy agent class policies.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-24
"""

from __future__ import annotations

from alembic import op


revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_class_policies")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_class_policies (
            agent_class TEXT PRIMARY KEY,
            rules JSONB NOT NULL DEFAULT '[]'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
