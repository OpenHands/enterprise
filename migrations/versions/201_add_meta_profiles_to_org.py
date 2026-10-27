"""Add meta_profiles column to org table.

Organization Model Routers (SDK meta-profiles) are stored at the organization
level, mirroring llm_profiles / provider_connections: a single blob holding
every router plus the active pointer. Envelope is
``{profiles: {<name>: MetaProfile}, active: <name> | null,
run_at_conversation_start: bool}`` (see OrgMetaProfiles).

Plain JSON rather than EncryptedJSON: a meta-profile carries prompts and the
names of saved LLM profiles, never credentials. The referenced LLM profiles
keep their keys in the encrypted ``llm_profiles`` column.

Data migration: no backfill. Existing orgs read back with meta_profiles = NULL,
which OrgMetaProfiles treats as an empty collection. The first save through the
/api/organizations/{org_id}/meta-profiles endpoints populates the column lazily.

Revision ID: 201
Revises: 200
Create Date: 2026-10-27
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '201'
down_revision: str | None = '200'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('org', sa.Column('meta_profiles', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('org', 'meta_profiles')
