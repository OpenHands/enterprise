"""Preserve the reverted user disabled flag revision.

Revision ID: 157
Revises: 156
Create Date: 2026-09-08 00:00:00.000000
"""

from typing import Sequence

revision: str = '157'
down_revision: str | None = '156'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
