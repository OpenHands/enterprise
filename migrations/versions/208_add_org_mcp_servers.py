"""Add ``mcp_servers`` to ``org`` for organization-scoped MCP server listings.

An org admin defines MCP server listings once (OHE-3091) and every member's
conversation launches with them. The column mirrors ``org.llm_profiles`` /
``org.agent_profiles``: an EncryptedJSON column (stored as String) holding
``{servers: {<name>: {managed_by: 'org' | 'user', server: <MCPServer>}}}``.
An ``org``-managed listing carries the admin's shared credential inline, so
the column is the at-rest encryption boundary.

It is deliberately not the retired ``org.mcp_config`` (migrations 103 / 108 /
137): that column broadcast one member's private servers to the whole org,
whereas this one only ever holds admin-authored listings and is never merged
into ``agent_settings`` -- the launch path adds the listings under each
member's own servers.

Data migration: no backfill. Existing rows read back as ``NULL`` (no listings).

Revision ID: 208
Revises: 207
Create Date: 2026-10-31
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '208'
down_revision: str | None = '207'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('org', sa.Column('mcp_servers', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('org', 'mcp_servers')
