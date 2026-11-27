"""Seed the Bitbucket Data Center login IDP.

Before OAuth v2, the login page showed a "Log in with Bitbucket Data Center"
button that went through Keycloak's ``bitbucket_data_center`` identity
provider (``kc_idp_hint=bitbucket_data_center``). Migration 189 seeds that
kind of row for GitHub, GitLab and Bitbucket Cloud only, so an install that
supports Bitbucket Data Center -- the only Bitbucket the Replicated install
offers -- lost its login button when it moved to OAuth v2.

This adds the missing row, built like migration 189's: it reuses the Keycloak
client, appends ``kc_idp_hint=bitbucket_data_center`` to ``authorization_url``
and sets ``legacy_keycloak_type`` so the callback stores the real Bitbucket
Data Center token (see ``OAuthProviderDetails.legacy_keycloak_type``).

The row is seeded only when the database has a Bitbucket Data Center git
provider row, ``KEYCLOAK_CLIENT_ID`` is set, and no login IDP already routes
to Bitbucket Data Center. Nothing is seeded without a Keycloak client to route
through, so an install whose migration environment lacks it has to add the row
by hand.

Revision ID: 224
Revises: 223
Create Date: 2026-11-25 00:00:00.000000
"""

from __future__ import annotations

import json
import os
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '224'
down_revision: str | None = '223'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OAUTH_KIND = 'OAuthProviderDetails'
_ENTERPRISE_SSO_PROVIDER_TYPE = 'enterprise_sso'
_PROVIDER_TYPE = 'bitbucket_data_center'
_TITLE = 'Log in with Bitbucket Data Center'
# Same blue as the legacy Bitbucket Data Center button in login-content.tsx.
_COLOR = '#2684FF'
# Rendered by the frontend as a standalone ``data:image/svg+xml`` <img> (see
# migration 189), so the fill is white rather than ``currentColor``.
_ICON = (
    '<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
    '<title>Bitbucket</title><path fill="#FFFFFF" d="M.778 1.213a.768.768 0 '
    '00-.768.892l3.263 19.81c.084.5.515.868 1.022.873H19.95a.772.772 0 '
    '00.77-.646l3.27-20.03a.768.768 0 00-.768-.891zM14.52 15.53H9.522L8.17 '
    '8.466h7.561z"/></svg>'
)


def _resolve_keycloak_realm_base_url() -> str:
    """Externally reachable Keycloak realm base URL.

    Duplicated from migration 189 (each migration re-derives this instead of
    importing a sibling). ``KEYCLOAK_SERVER_URL`` is the in-cluster address and
    is only used as a last resort, after the URLs browsers can reach.
    """
    auth_url = os.getenv('AUTH_URL', '').strip().rstrip('/')
    if not auth_url:
        auth_web_host = os.getenv('AUTH_WEB_HOST', '').strip()
        if not auth_web_host:
            web_host = os.getenv('WEB_HOST', '').strip()
            if web_host:
                auth_web_host = f'auth.{web_host}'
        if auth_web_host:
            auth_url = f'https://{auth_web_host}'

    if not auth_url:
        auth_url = os.getenv('KEYCLOAK_SERVER_URL_EXT', '')
    if not auth_url:
        auth_url = os.getenv('KEYCLOAK_SERVER_URL', '')

    kc_realm = os.getenv('KEYCLOAK_REALM_NAME', '').strip()
    if not auth_url or not kc_realm:
        return ''
    return f'{auth_url}/realms/{kc_realm}'


def _keycloak_details_from_env() -> dict | None:
    """Keycloak client details, or ``None`` when there is no Keycloak client."""
    kc_client_id = os.getenv('KEYCLOAK_CLIENT_ID', '').strip()
    if not kc_client_id:
        return None

    from storage.encrypt_utils import encrypt_value

    kc_client_secret = os.getenv('KEYCLOAK_CLIENT_SECRET', '').strip()
    kc_base = _resolve_keycloak_realm_base_url()
    authorization_url = (
        f'{kc_base}/protocol/openid-connect/auth?kc_idp_hint={_PROVIDER_TYPE}'
        if kc_base
        else None
    )
    return {
        'kind': _OAUTH_KIND,
        'provider_type': _ENTERPRISE_SSO_PROVIDER_TYPE,
        'client_id': kc_client_id,
        'client_secret': encrypt_value(kc_client_secret) if kc_client_secret else None,
        'authorization_url': authorization_url,
        'token_url': (f'{kc_base}/protocol/openid-connect/token' if kc_base else None),
        'userinfo_url': (
            f'{kc_base}/protocol/openid-connect/userinfo' if kc_base else None
        ),
        'scopes': ['openid', 'email', 'profile'],
        'permitted_drift_seconds': 60,
        'legacy_keycloak_type': _PROVIDER_TYPE,
    }


def _has_git_provider_row(bind) -> bool:
    return (
        bind.execute(
            sa.text(
                """
                SELECT 1 FROM auth_providers
                WHERE kind = :kind AND is_idp = false
                  AND details ->> 'provider_type' = :provider_type
                LIMIT 1
                """
            ),
            {'kind': _OAUTH_KIND, 'provider_type': _PROVIDER_TYPE},
        ).scalar_one_or_none()
        is not None
    )


def _has_login_row(bind) -> bool:
    return (
        bind.execute(
            sa.text(
                """
                SELECT 1 FROM auth_providers
                WHERE kind = :kind AND is_idp = true
                  AND (details ->> 'provider_type' = :provider_type
                       OR details ->> 'legacy_keycloak_type' = :provider_type)
                LIMIT 1
                """
            ),
            {'kind': _OAUTH_KIND, 'provider_type': _PROVIDER_TYPE},
        ).scalar_one_or_none()
        is not None
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    details = _keycloak_details_from_env()
    if details is None or not _has_git_provider_row(bind) or _has_login_row(bind):
        return

    # ``enabled`` is set explicitly: migration 196 made it default to
    # ``false``, so an insert that omitted it would be hidden from the login
    # page.
    bind.execute(
        sa.text(
            """
            INSERT INTO auth_providers
                (kind, title, is_idp, icon, color, allow_registration, enabled,
                 details, created_at, updated_at)
            VALUES
                (:kind, :title, true, :icon, :color, true, true, :details,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        ),
        {
            'kind': _OAUTH_KIND,
            'title': _TITLE,
            'icon': _ICON,
            'color': _COLOR,
            'details': json.dumps(details),
        },
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            DELETE FROM auth_providers
            WHERE kind = :kind AND is_idp = true AND title = :title
              AND details ->> 'legacy_keycloak_type' = :provider_type
            """
        ),
        {'kind': _OAUTH_KIND, 'title': _TITLE, 'provider_type': _PROVIDER_TYPE},
    )
