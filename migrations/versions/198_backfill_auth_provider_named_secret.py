"""Backfill ``details.named_secret`` on git-provider ``auth_providers`` rows.

``OAuthProviderDetails.named_secret`` names the conversation secret that
carries a linked user's access token into the sandbox. Before it existed, a
git provider's token was always exposed as ``<PROVIDER_TYPE>_TOKEN`` (e.g.
``GITHUB_TOKEN``), and login IDP tokens were never exposed. Setting that name
on every existing git-provider row (``is_idp = false``) keeps both behaviors.
Rows that already have a ``named_secret`` are left alone.

Revision ID: 198
Revises: 197
Create Date: 2026-10-26 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '198'
down_revision: str | None = '197'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE auth_providers
            SET details = (
                    details::jsonb
                    || jsonb_build_object(
                        'named_secret',
                        upper(details ->> 'provider_type') || '_TOKEN'
                    )
                )::json,
                updated_at = CURRENT_TIMESTAMP
            WHERE kind = 'OAuthProviderDetails' AND is_idp = false
              AND details ->> 'provider_type' IS NOT NULL
              AND details ->> 'provider_type' <> 'enterprise_sso'
              AND details ->> 'legacy_keycloak_type' IS NULL
              AND details ->> 'named_secret' IS NULL
            """
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE auth_providers
            SET details = (details::jsonb - 'named_secret')::json,
                updated_at = CURRENT_TIMESTAMP
            WHERE kind = 'OAuthProviderDetails'
              AND details::jsonb -> 'named_secret' IS NOT NULL
            """
        )
    )
