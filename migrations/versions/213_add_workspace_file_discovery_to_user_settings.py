"""Add ``workspace_file_discovery`` to ``user_settings``.

Persisted workspace file-discovery preferences keyed by absolute working dir
(enterprise#853 / software-agent-sdk#6079). Stored as raw JSON and normalized
against server ceilings at read time in
``openhands.app_server.app_conversation.workspace_file_discovery.normalize_options``,
so a corrupt or oversized stored value can never disable the bound or the
mandatory security/bookkeeping exclusions.

The column lives on the user's row only (never the org) so preferences are
personal and cannot leak across tenants or org members.

Data migration: no backfill. Existing rows read back as ``NULL`` (no
preferences), which normalizes to the bounded defaults.

Revision ID: 213
Revises: 212
Create Date: 2026-11-04
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSON

revision: str = '213'
down_revision: str | None = '212'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'user_settings',
        sa.Column('workspace_file_discovery', JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('user_settings', 'workspace_file_discovery')
