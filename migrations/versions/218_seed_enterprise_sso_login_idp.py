"""Seed the Enterprise SSO (SAML) login IDP.

Migration 189 seeds one login IDP row per federated git-provider brand
(GitHub/GitLab/Bitbucket) so the OAuth v2 login page keeps showing the
buttons the legacy ``PROVIDERS_CONFIGURED`` login page did. The legacy login
page also showed an Enterprise SSO button whenever ``ENABLE_ENTERPRISE_SSO``
was set (``server.config.ServerConfig.get_config``), but nothing seeded a row
for it, so installs that enable SAML in the Replicated config lose that button
once ``ENABLE_OAUTH_V2_LOGIN`` is on.

SAML terminates at Keycloak: the chart's ``keycloak-config`` init container
imports the IdP metadata as the Keycloak identity provider ``enterprise_sso``.
OpenHands only ever sees an OIDC login through the Keycloak client, so, like
migration 189's rows, this one reuses that client and appends
``kc_idp_hint=enterprise_sso`` to ``authorization_url`` so Keycloak skips its
chooser. Unlike them it has no ``legacy_keycloak_type``: there is no git
provider token to fetch behind an enterprise SSO login.

The row is only seeded when ``ENABLE_ENTERPRISE_SSO`` is truthy and
``KEYCLOAK_CLIENT_ID`` is set. ``title`` comes from
``ENTERPRISE_SSO_DISPLAY_NAME`` (the Replicated "Identity Provider Display
Name" setting) and falls back to ``Company SSO`` -- the Replicated default --
when the migration environment does not carry it. Admins can retitle the row
afterwards.

``enabled`` is set explicitly: migration 196 made it default to ``false``, so
an insert that omitted it would create a row that is hidden from the login
page.

Revision ID: 218
Revises: 217
Create Date: 2026-11-18 00:00:00.000000
"""

from __future__ import annotations

import json
import os
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '218'
down_revision: str | None = '217'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OAUTH_KIND = 'OAuthProviderDetails'
_ENTERPRISE_SSO_PROVIDER_TYPE = 'enterprise_sso'
_IDP_HINT = 'enterprise_sso'
_DEFAULT_TITLE = 'Company SSO'
_COLOR = '#4F46E5'
# Rendered by the frontend as a standalone ``data:image/svg+xml`` <img> (see
# migration 189), so the fill is white rather than ``currentColor``.
_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" '
    'viewBox="0 0 24 24" fill="none"><path fill="#FFFFFF" d="M12 1 3 5v6c0 '
    '5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4zm0 10.99h7c-.53 '
    '4.12-3.28 7.79-7 8.94V12H5V6.3l7-3.11v8.8z"/></svg>'
)
_HINT_REGEX = f'kc_idp_hint={_IDP_HINT}($|&)'


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


def _enterprise_sso_details_from_env() -> dict | None:
    """Keycloak client details for the Enterprise SSO row, from the environment.

    ``None`` when ``ENABLE_ENTERPRISE_SSO`` is off or there is no Keycloak
    client (``KEYCLOAK_CLIENT_ID``) to route the login through.
    """
    if os.getenv('ENABLE_ENTERPRISE_SSO', '').strip().lower() not in ('true', '1'):
        return None
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


def _enterprise_sso_row_exists(bind) -> bool:
    """Whether a login IDP already routes to the Keycloak ``enterprise_sso``
    identity provider. Matched on the hint, not on ``provider_type`` alone, so
    an admin-added direct OIDC ``enterprise_sso`` row does not suppress it."""
    return (
        bind.execute(
            sa.text(
                """
                SELECT 1 FROM auth_providers
                WHERE kind = :kind AND is_idp = true
                  AND details ->> 'authorization_url' ~ :hint_regex
                LIMIT 1
                """
            ),
            {'kind': _OAUTH_KIND, 'hint_regex': _HINT_REGEX},
        ).scalar_one_or_none()
        is not None
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    details = _enterprise_sso_details_from_env()
    if details is None or _enterprise_sso_row_exists(bind):
        return

    title = os.getenv('ENTERPRISE_SSO_DISPLAY_NAME', '').strip() or _DEFAULT_TITLE
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
            'title': title,
            'icon': _ICON,
            'color': _COLOR,
            'details': json.dumps(details),
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM auth_providers
            WHERE kind = :kind AND is_idp = true
              AND details ->> 'provider_type' = :provider_type
              AND details ->> 'authorization_url' ~ :hint_regex
            """
        ),
        {
            'kind': _OAUTH_KIND,
            'provider_type': _ENTERPRISE_SSO_PROVIDER_TYPE,
            'hint_regex': _HINT_REGEX,
        },
    )
