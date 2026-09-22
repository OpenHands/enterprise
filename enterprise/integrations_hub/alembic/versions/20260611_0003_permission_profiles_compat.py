"""Keep permission profile migration history compatible.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-11
"""

from __future__ import annotations


revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
