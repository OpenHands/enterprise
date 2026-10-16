"""Convert already-seeded per-brand Keycloak login IDPs to ``enterprise_sso``.

Migration 189 originally seeded the ``Log in with GitHub/GitLab/Bitbucket``
rows with ``details.provider_type`` set to the brand (and, in a later
revision, ``details.legacy_keycloak = true``). Those rows are really
Keycloak connectors, so 189 now seeds them with
``provider_type='enterprise_sso'`` and ``details.legacy_keycloak_type =
<brand>`` instead (see ``OAuthProviderDetails.legacy_keycloak_type``).

Databases that already ran the earlier 189 never see that change, so this
migration rewrites their rows to the new shape. A row is converted when it is
an ``is_idp`` OAuth row whose ``provider_type`` is one of the three brands
*and* whose ``authorization_url`` carries the matching ``kc_idp_hint`` --
which distinguishes the seeded Keycloak rows from a direct, admin-configured
brand IDP. On databases that ran the new 189 nothing matches, so this is a
no-op.

Revision ID: 190
Revises: 189
Create Date: 2026-10-16 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '190'
down_revision: str | None = '189'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    bind.execute(
        sa.text(
            """
            UPDATE auth_providers
            SET details = (
                    (details::jsonb - 'legacy_keycloak')
                    || jsonb_build_object(
                        'provider_type', 'enterprise_sso',
                        'legacy_keycloak_type', details ->> 'provider_type'
                    )
                )::json,
                updated_at = CURRENT_TIMESTAMP
            WHERE kind = 'OAuthProviderDetails' AND is_idp = true
              AND details ->> 'provider_type' IN ('github', 'gitlab', 'bitbucket')
              AND details ->> 'authorization_url'
                  ~ ('kc_idp_hint=' || (details ->> 'provider_type') || '($|&)')
            """
        )
    )


def downgrade() -> None:
    # Migration 189 now seeds the converted shape itself, and its downgrade
    # removes rows in that shape, so there is nothing to revert here.
    pass
