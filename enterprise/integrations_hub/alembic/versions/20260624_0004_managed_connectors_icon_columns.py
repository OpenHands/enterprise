"""Add icon_bg and icon_color to managed_connectors.

The initial schema migration created ``managed_connectors`` without the
``icon_bg``/``icon_color`` columns. They were later added to the model and to
the ``CREATE TABLE IF NOT EXISTS`` statement, but because the table already
existed in production the no-op create never added them, and no ``ALTER TABLE``
migration existed. As a result ``save_managed_connector`` failed with
``UndefinedColumn`` whenever it inserted/updated a managed connector, which
silently broke tool re-indexing (e.g. the Notion connector could never be
re-indexed to pick up ``config.inputSchema``).

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-24
"""

from __future__ import annotations

from alembic import op


revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE managed_connectors "
        "ADD COLUMN IF NOT EXISTS icon_bg TEXT, "
        "ADD COLUMN IF NOT EXISTS icon_color TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE managed_connectors "
        "DROP COLUMN IF EXISTS icon_color, "
        "DROP COLUMN IF EXISTS icon_bg"
    )
