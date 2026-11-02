"""Add ``org_mcp_oauth_states`` to ``org_member`` for user-managed OAuth listings.

A ``user``-managed organization MCP server listing (OHE-3091) may authenticate
with OAuth. The listing on ``org.mcp_servers`` carries only the OAuth client
configuration; the tokens each member obtains by authorizing with their own
account live here, keyed by listing name: ``{<name>: <MCPOAuthState dump>}``.
EncryptedJSON (stored as String): the column holds access and refresh tokens,
so it is an at-rest encryption boundary like ``org_member.mcp_config``.

Data migration: no backfill. ``NULL`` reads as "authorized nothing".

Revision ID: 208
Revises: 207
Create Date: 2026-11-02
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '208'
down_revision: str | None = '207'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'org_member', sa.Column('org_mcp_oauth_states', sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('org_member', 'org_mcp_oauth_states')
