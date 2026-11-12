"""Seed the ``Login with Enterprise SSO`` login IDP.

Before the OAuth v2 login path, ``/login`` showed a ``Login with Enterprise
SSO`` button whenever ``ENABLE_ENTERPRISE_SSO`` was set (see
``server.config.ServerConfig.get_config`` and
``frontend/src/components/features/auth/login-content.tsx``), alongside any
configured git-provider buttons. It went through the Keycloak client with
``kc_idp_hint=enterprise_sso``, i.e. to the customer's own SSO/OIDC identity
provider, which by convention carries the Keycloak alias ``enterprise_sso``.

Migration 189 only reproduced the GitHub/GitLab/Bitbucket buttons, so installs
relying on a custom Keycloak SSO provider lost their entry point once
``ENABLE_OAUTH_V2_LOGIN`` rendered ``auth_providers`` rows. This adds the
missing row, shaped like 189's: the Keycloak client's credentials and
endpoints with ``kc_idp_hint=enterprise_sso`` on ``authorization_url``. It has
no ``legacy_keycloak_type`` because the identity is the customer's SSO, not a
git provider, so there is no git token to fetch.

Gated like the legacy button: ``ENABLE_ENTERPRISE_SSO`` set to any non-empty
value (``config.py`` does the same, so ``'false'`` also counts as enabled), and
``KEYCLOAK_CLIENT_ID`` set (otherwise there is no Keycloak client to route
through). Skipped if a row with that hint already exists.

Revision ID: 193
Revises: 192
Create Date: 2026-10-20 00:00:00.000000
"""

from __future__ import annotations

import json
import os
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '193'
down_revision: str | None = '192'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OAUTH_KIND = 'OAuthProviderDetails'
_ENTERPRISE_SSO_PROVIDER_TYPE = 'enterprise_sso'
_IDP_HINT = 'enterprise_sso'
_TITLE = 'Login with Enterprise SSO'
# Legacy button color (``bg-[#374151]``) and icon (``FaUserShield``).
_COLOR = '#374151'
# Font Awesome Free 5 "user-shield" (CC BY 4.0, https://fontawesome.com/license/free),
# filled white because the frontend renders `icon` as a standalone
# `data:image/svg+xml` <img>, where `currentColor` would resolve to black.
_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 512">'
    '<path fill="#FFFFFF" d="M622.3 271.1l-115.2-45c-4.1-1.6-12.6-3.7-22.2 '
    '0l-115.2 45c-10.7 4.2-17.7 14-17.7 24.9 0 111.6 68.7 188.8 132.9 '
    '213.9 9.6 3.7 18 1.6 22.2 0C558.4 489.9 640 420.5 640 296c0-10.9-7-20.7'
    '-17.7-24.9zM496 462.4V273.3l95.5 37.3c-5.6 87.1-60.9 135.4-95.5 '
    '151.8zM224 256c70.7 0 128-57.3 128-128S294.7 0 224 0 96 57.3 96 128s57.3'
    ' 128 128 128zm96 40c0-2.5.8-4.8 1.1-7.2-2.5-.1-4.9-.8-7.5-.8h-16.7c-22.2'
    ' 10.2-46.9 16-72.9 16s-50.6-5.8-72.9-16h-16.7C60.2 288 0 348.2 0 '
    '422.4V464c0 26.5 21.5 48 48 48h352c6.8 0 13.3-1.5 19.2-4-54-42.9-99.2'
    '-116.7-99.2-212z"/></svg>'
)

# Matches the hint as a whole query parameter value, not a prefix of another.
_HINT_URL_REGEX = f'kc_idp_hint={_IDP_HINT}($|&)'


def _resolve_keycloak_realm_base_url() -> str:
    """Resolve the externally-reachable Keycloak realm base URL.

    Duplicated from migration 189 (this directory re-derives sibling logic
    rather than importing it). ``KEYCLOAK_SERVER_URL`` is the in-cluster URL
    and is only a last resort here, as in 189.
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


def _enterprise_sso_details_from_env() -> dict | None:
    kc_client_id = os.getenv('KEYCLOAK_CLIENT_ID', '').strip()
    if not kc_client_id:
        return None

    from storage.encrypt_utils import encrypt_value

    kc_client_secret = os.getenv('KEYCLOAK_CLIENT_SECRET', '').strip()
    kc_base = _resolve_keycloak_realm_base_url()
    authorization_url = (
        f'{kc_base}/protocol/openid-connect/auth?kc_idp_hint={_IDP_HINT}'
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
    }


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    if not os.getenv('ENABLE_ENTERPRISE_SSO', '').strip():
        return

    details = _enterprise_sso_details_from_env()
    if details is None:
        return

    already_seeded = bind.execute(
        sa.text(
            """
            SELECT 1 FROM auth_providers
            WHERE kind = :kind AND is_idp = true
              AND details ->> 'provider_type' = :provider_type
              AND details ->> 'authorization_url' ~ :hint_regex
            LIMIT 1
            """
        ),
        {
            'kind': _OAUTH_KIND,
            'provider_type': _ENTERPRISE_SSO_PROVIDER_TYPE,
            'hint_regex': _HINT_URL_REGEX,
        },
    ).scalar_one_or_none()
    if already_seeded is not None:
        return

    bind.execute(
        sa.text(
            """
            INSERT INTO auth_providers
                (kind, title, is_idp, icon, color, allow_registration,
                 details, created_at, updated_at)
            VALUES
                (:kind, :title, true, :icon, :color, true, :details,
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
    # Narrowly matched on title and color as well as the hint, so an admin's
    # own enterprise-SSO row that merely shares the hint is left alone.
    op.get_bind().execute(
        sa.text(
            """
            DELETE FROM auth_providers
            WHERE kind = :kind AND is_idp = true AND title = :title
              AND color = :color
              AND details ->> 'provider_type' = :provider_type
              AND details ->> 'authorization_url' ~ :hint_regex
            """
        ),
        {
            'kind': _OAUTH_KIND,
            'title': _TITLE,
            'color': _COLOR,
            'provider_type': _ENTERPRISE_SSO_PROVIDER_TYPE,
            'hint_regex': _HINT_URL_REGEX,
        },
    )
