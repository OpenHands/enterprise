"""Request ``offline_access`` on Azure DevOps ``auth_providers`` rows.

Microsoft Entra's v2.0 endpoint only issues a refresh token when the
authorization request includes ``offline_access``. Migration 168 seeded the
Azure DevOps git provider with just the Azure DevOps ``.default`` scope, so a
linked token could not be refreshed and stopped working when the access token
expired (60-90 minutes). Users linked before this migration have no refresh
token stored and must reconnect once to get one.

Only rows whose ``scopes`` is a list are touched: a row with no scopes requests
nothing from Entra and needs an admin to configure it.

Revision ID: 206
Revises: 205
Create Date: 2026-10-29 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '206'
down_revision: str | None = '205'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE auth_providers
            SET details = jsonb_set(
                    details::jsonb,
                    '{scopes}',
                    (details::jsonb -> 'scopes') || '["offline_access"]'::jsonb
                )::json,
                updated_at = CURRENT_TIMESTAMP
            WHERE kind = 'OAuthProviderDetails'
              AND details ->> 'provider_type' = 'azure_devops'
              AND jsonb_typeof(details::jsonb -> 'scopes') = 'array'
              AND NOT (details::jsonb -> 'scopes') ? 'offline_access'
            """
        )
    )


def downgrade() -> None:
    # The scope is indistinguishable from one an admin added, and removing it
    # would stop refresh tokens from being issued again.
    pass
