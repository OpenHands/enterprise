"""Add agent_profiles to org_member for personal-scoped Agent Profiles.

Personal (member-scoped) Agent Profiles (OHE-3456) live on the ``org_member``
row, next to the member's own ``mcp_config`` -- the user-scoped MCP servers a
personal profile references. The column mirrors ``org.agent_profiles``:

- ``org_member.agent_profiles`` is an EncryptedJSON column (stored as String)
  holding the same ``{profiles: {<id>: AgentProfile}, active}`` envelope as the
  org collection. The envelope's ``active`` is unused for personal profiles:
  ``org_member.active_agent_profile_id`` stays the single launch pointer and may
  reference a personal or an org profile id.

Data migration: no backfill. Existing rows read back as ``NULL`` (an empty
collection); a member sees only the org profiles until they save a personal one.

Revision ID: 199
Revises: 198
Create Date: 2026-10-24
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '199'
down_revision: str | None = '198'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('org_member', sa.Column('agent_profiles', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('org_member', 'agent_profiles')
