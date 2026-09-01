"""Add provider_connections column to org table.

Shared LLM provider connections are stored at the organization level, mirroring
llm_profiles / agent_profiles: a single encrypted blob so one credential
can be referenced by many LLM profiles.

The column uses EncryptedJSON (stored as String) because a connection carries an
api_key that must be encrypted at rest.

Data migration: no backfill. Existing orgs read back with
provider_connections = NULL, which ProviderConnections treats as an empty
collection. The first save through the
/api/organizations/{org_id}/provider-connections endpoints populates the
column lazily, so no downtime or follow-up script is required.

Revision ID: 155
Revises: 154
Create Date: 2026-08-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '155'
down_revision: Union[str, None] = '154'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('org', sa.Column('provider_connections', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('org', 'provider_connections')
