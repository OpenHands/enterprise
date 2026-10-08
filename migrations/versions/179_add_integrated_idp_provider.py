"""Seed the integrated/password IDP as a real ``oauth_providers`` row.

The integrated IDP (``server.routes.idp`` — email+password login with no
external OAuth/OIDC provider) used to be modeled as an in-memory sentinel
(``IdpProvider``, id ``-1``) gated by the ``ENABLE_INTEGRATED_IDP`` env var,
re-checked on every request (see migration 176). That meant every IDP-token
refresh had to probe every currently-configured ``is_idp`` row and fall back
to a global "does any real IDP exist" guess to tell "this session never had
IDP tokens" apart from "this session's token row is stale" -- a guess that
breaks as soon as a real IDP is configured alongside the integrated one: the
sentinel stops being returned by ``get_idp_providers()``, so every
integrated-IDP session starts looking like a stale real-IDP session.

This migration makes the integrated IDP a real row, like every other
provider, removing the need to special-case it at all: whether it is
"available" is simply whether this row exists, and sessions authenticated
through it carry its real ``id`` (not a sentinel) wherever a provider id is
needed, including the ``openhands_auth`` cookie.

``ENABLE_INTEGRATED_IDP`` is read here, once, to decide whether to seed the
row -- it is no longer consulted anywhere else; subsequent enable/disable
goes through the (planned) ``oauth_providers`` CRUD API instead of this env
var. Hosts that never set the env var get no row and the feature stays
exactly as unavailable as it is today. The row has no ``client_secret`` /
``token_url`` / ``authorization_url`` -- there is no external OAuth flow to
drive, mirroring the sentinel's previous defaults.

Revision ID: 179
Revises: 178
Create Date: 2026-10-08 00:00:00.000000
"""

import os
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '179'
down_revision: str | None = '178'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors storage.oauth_provider.INTEGRATED_IDP_CATEGORY. Duplicated (not
# imported) so this migration's behavior stays fixed in time even if the app
# constant's module location changes later.
_INTEGRATED_IDP_CATEGORY = 'integrated_idp'


def _is_integrated_idp_enabled() -> bool:
    """Mirrors the old ``server.constants.ENABLE_INTEGRATED_IDP`` parsing.

    Accepts both ``'true'`` and ``'1'`` (older Helm charts default to ``'1'``).
    """
    return os.getenv('ENABLE_INTEGRATED_IDP', 'false').strip().lower() in (
        'true',
        '1',
    )


def upgrade() -> None:
    if not _is_integrated_idp_enabled():
        return

    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    inspector = sa.inspect(bind)
    if not inspector.has_table('oauth_providers'):
        # Should not happen (migration 168 creates it) but guards against
        # running this out of order.
        return

    existing = bind.execute(
        sa.text(
            'SELECT 1 FROM oauth_providers WHERE provider_category = :category LIMIT 1'
        ),
        {'category': _INTEGRATED_IDP_CATEGORY},
    ).scalar_one_or_none()
    if existing is not None:
        return

    bind.execute(
        sa.text(
            """
            INSERT INTO oauth_providers
                (provider_category, display_name, is_idp, client_id,
                 client_secret, authorization_url, token_url, userinfo_url,
                 scopes, permitted_drift_seconds, created_at, updated_at)
            VALUES
                (:provider_category, :display_name, true, :client_id,
                 NULL, NULL, NULL, NULL,
                 NULL, 60, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        ),
        {
            'provider_category': _INTEGRATED_IDP_CATEGORY,
            'display_name': 'Password Login',
            'client_id': 'integrated-idp',
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text('DELETE FROM oauth_providers WHERE provider_category = :category'),
        {'category': _INTEGRATED_IDP_CATEGORY},
    )
