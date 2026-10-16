"""Seed GitHub/GitLab/Bitbucket login IDPs, retire the generic Keycloak row,
and rebrand the integrated/password login.

**Federated git-provider login IDPs.** Migration 168 originally seeded a
single ``auth_providers`` IDP row for Keycloak itself
(``provider_type='enterprise_sso'``, ``title='Keycloak'``) whenever
``KEYCLOAK_CLIENT_ID`` was set -- which, since Keycloak is the auth server
for every OpenHands Enterprise install, was effectively always. That row has
no ``kc_idp_hint`` on its ``authorization_url``, so visiting it lands on
Keycloak's own login screen rather than any specific brand.

Before the OAuth v2 login path existed, the ``/login`` page instead showed
one hard-coded button per configured git-provider brand (``GitHub``/
``GitLab``/``Bitbucket`` -- see
``frontend/src/components/features/auth/login-content.tsx`` and
``frontend/src/utils/generate-auth-url.ts``), each of which *also* went
through Keycloak's authorization endpoint, just with
``kc_idp_hint=<brand>`` appended so Keycloak skipped its chooser and went
straight to that brand's federated identity provider. Which buttons showed
mirrored ``server.config.ServerConfig.get_config``'s ``PROVIDERS_CONFIGURED``
-- gated on each brand's ``*_APP_CLIENT_ID`` env var (never the matching
``*_APP_CLIENT_SECRET`` -- that secret authenticates the *git* OAuth app used
for repo access after login, a completely separate flow from the login
IDP's own client/secret, and is never read here).

Once ``ENABLE_OAUTH_V2_LOGIN`` renders every ``is_idp`` row as a button
(``GET /oauth/idp-providers``), the single generic ``'Keycloak'`` row was the
*only* thing that showed -- a regression from the per-brand chooser above.
This migration closes that gap: for each of GitHub/GitLab/Bitbucket whose
``*_APP_CLIENT_ID`` is set (and only if ``KEYCLOAK_CLIENT_ID`` is also set --
there is no Keycloak client to route through otherwise), it adds a new
``is_idp=True`` row that:

- Reuses the Keycloak client's ``client_id``/``client_secret``/
  ``token_url``/``userinfo_url``/``scopes``, resolved directly from the same
  ``KEYCLOAK_*``/``AUTH_URL``/``AUTH_WEB_HOST``/``WEB_HOST`` env vars
  migration 168 used to (see ``_resolve_keycloak_realm_base_url``/
  ``_keycloak_details_from_env``, duplicated from -- no longer present in --
  migration 168, same convention as every other migration in this directory
  re-deriving a sibling migration's logic rather than importing it).
  Customers keep using their existing Keycloak client; this never talks to
  GitHub/GitLab/Bitbucket's own OAuth endpoints directly.
- Appends ``kc_idp_hint=<brand>`` to ``authorization_url`` (the same query param
  ``generate-auth-url.ts`` added client-side for the legacy buttons) so
  ``GET /oauth/{id}/login`` (``server/routes/oauth_v2.py``) sends the
  browser straight to that brand's federated login at Keycloak -- no code
  change needed there, since it already appends its own query params after
  whatever is already on ``authorization_url``.
- Carries that brand's button color/icon from the legacy, hard-coded
  buttons above, so the "Let's Get Started" screen looks the same as before
  OAuth v2.
- Keeps ``provider_type='enterprise_sso'`` (it *is* a Keycloak connector) and
  sets ``details.legacy_keycloak_type = <brand>`` so
  ``server.routes.oauth_v2``'s callback knows this row's ``token_url`` only
  ever yields a Keycloak token, not the brand's real one, and fetches/stores
  the real token against the git-provider row for that brand instead -- see
  ``OAuthProviderDetails.legacy_keycloak_type``.

**Retiring the generic Keycloak row.** This migration also deletes the old
generic Keycloak row outright, if a host still has one from an older version
of migration 168 (matched narrowly on ``title='Keycloak'`` + ``provider_type
='enterprise_sso'``, so an admin-added second enterprise-SSO connector with
a different title is never touched) -- it added no value once there is at
least one properly-branded row, and matches the legacy login page, which
never showed a generic "Keycloak" button either. An install with no
git-provider brand configured, and no admin-added enterprise-SSO/SAML/
integrated-password row, now has *no* login IDP at all rather than a
poorly-labeled fallback -- the same "no providers configured" outcome the
legacy login page already showed in that situation, so this is not a new
failure mode.

**Rebranding the integrated/password login.** Migration 179's seeded
``title`` (``'Password Login'``) and migration 188's blanket ``color``
default (black, ``#000000``) are both fixed at their point of origin for
new installs (179's literal is now ``'Login with Password'``, 188 now also
overrides this one kind's color to ``#03A956`` right after adding the
column -- see those migrations). Hosts that already ran them before this
fix are repaired here, narrowly matching the pre-fix defaults
(``title='Password Login'`` AND ``color='#000000'``) so an admin who already
customized either field is left alone.

Revision ID: 189
Revises: 188
Create Date: 2026-10-15 00:00:00.000000
"""

from __future__ import annotations

import json
import os
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '189'
down_revision: str | None = '188'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OAUTH_KIND = 'OAuthProviderDetails'
_ENTERPRISE_SSO_PROVIDER_TYPE = 'enterprise_sso'
_KEYCLOAK_TITLE = 'Keycloak'

_PASSWORD_KIND = 'PasswordProviderDetails'
_PASSWORD_LOGIN_OLD_TITLE = 'Password Login'
_PASSWORD_LOGIN_NEW_TITLE = 'Login with Password'
_PASSWORD_LOGIN_OLD_COLOR = '#000000'
_PASSWORD_LOGIN_NEW_COLOR = '#03A956'

# Mirrors the raw SVG markup of frontend/src/assets/branding/{brand}-logo.svg,
# with `fill="currentColor"` resolved to white: these rows' `icon` is
# rendered by the frontend as a standalone `data:image/svg+xml` <img> (see
# migration 188 / login-content.tsx's `V2ProviderButton`), never inlined, so
# `currentColor` would resolve to the SVG's own initial color (black) rather
# than inheriting the button's white text color.
_GITHUB_ICON = (
    '<svg width="22" height="22" viewBox="0 0 22 22" fill="none" '
    'xmlns="http://www.w3.org/2000/svg"><path d="M12 2.25C6.624 2.25 2.25 '
    '6.624 2.25 12C2.25 16.376 5.115 20.073 9.057 21.428C9.563 21.514 9.747 '
    '21.211 9.747 20.95C9.747 20.713 9.738 19.991 9.738 19.153C7 19.713 6.4 '
    '18.45 6.4 18.45C5.952 17.387 5.328 17.084 5.328 17.084C4.476 16.487 '
    '5.387 16.487 5.387 16.487C6.328 16.546 6.85 17.481 6.85 17.481C7.675 '
    '18.862 9.057 18.487 9.776 18.226C9.867 17.603 10.133 17.179 10.419 '
    '16.94C8.287 16.713 6.05 15.85 6.05 11.9C6.05 10.837 6.4 9.975 6.859 '
    '9.3C6.759 9.05 6.45 8.038 6.95 6.65C6.95 6.65 7.734 6.4 9.738 7.775C'
    '10.483 7.534 11.25 7.413 12.017 7.413C12.784 7.413 13.55 7.534 14.296 '
    '7.775C16.3 6.4 17.084 6.65 17.084 6.65C17.584 8.038 17.275 9.05 17.175 '
    '9.3C17.634 9.975 17.984 10.837 17.984 11.9C17.984 15.85 15.747 16.7 '
    '13.615 16.94C13.975 17.237 14.296 17.813 14.296 18.7C14.296 19.975 '
    '14.287 20.6 14.287 20.95C14.287 21.211 14.471 21.514 14.977 21.428C'
    '18.919 20.073 21.784 16.376 21.784 12C21.784 6.624 17.376 2.25 12 2.25Z" '
    'fill="#FFFFFF"/></svg>'
)
_GITLAB_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="97 99 186 182">'
    '<path fill="#FFFFFF" d="M282.83,170.73l-.27-.69-26.14-68.22a6.81,6.81,0,'
    '0,0-2.69-3.24,7,7,0,0,0-8,.43,7,7,0,0,0-2.32,3.52l-17.65,54H154.29l-17.'
    '65-54A6.86,6.86,0,0,0,134.32,99a7,7,0,0,0-8-.43,6.87,6.87,0,0,0-2.69,3.'
    '24L97.44,170l-.26.69a48.54,48.54,0,0,0,16.1,56.1l.09.07.24.17,39.82,29.'
    '82,19.7,14.91,12,9.06a8.07,8.07,0,0,0,9.76,0l12-9.06,19.7-14.91,40.06-3'
    '0,.1-.08A48.56,48.56,0,0,0,282.83,170.73Z"/></svg>'
)
_BITBUCKET_ICON = (
    '<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
    '<title>Bitbucket</title><path fill="#FFFFFF" d="M.778 1.213a.768.768 0 '
    '00-.768.892l3.263 19.81c.084.5.515.868 1.022.873H19.95a.772.772 0 '
    '00.77-.646l3.27-20.03a.768.768 0 00-.768-.891zM14.52 15.53H9.522L8.17 '
    '8.466h7.561z"/></svg>'
)

# (provider_type, title, color, icon, gating env var). Title/color mirror the
# legacy per-brand buttons exactly (``I18nKey.GITHUB$CONNECT_TO_GITHUB`` etc.
# and the ``bg-[...]`` classes in ``login-content.tsx``); the gating env var
# mirrors ``server.config.ServerConfig.get_config``'s ``PROVIDERS_CONFIGURED``.
_FEDERATED_BRANDS: list[tuple[str, str, str, str, str]] = [
    ('github', 'Log in with GitHub', '#9E28B0', _GITHUB_ICON, 'GITHUB_APP_CLIENT_ID'),
    ('gitlab', 'Log in with GitLab', '#FC6B0E', _GITLAB_ICON, 'GITLAB_APP_CLIENT_ID'),
    (
        'bitbucket',
        'Log in with Bitbucket',
        '#2684FF',
        _BITBUCKET_ICON,
        'BITBUCKET_APP_CLIENT_ID',
    ),
]


def _resolve_keycloak_realm_base_url() -> str:
    """Resolve the externally-reachable Keycloak realm base URL.

    Duplicated from (no longer present in) migration 168 -- see this
    migration's module docstring. ``KEYCLOAK_SERVER_URL`` is the in-cluster
    URL used for server-to-server admin calls; it is not reachable by end
    users' browsers, so it must never be used to build the
    ``authorization_url``/``token_url``/``userinfo_url`` stored here. Those
    are derived instead from:

    - ``AUTH_URL``, if set, else
    - ``https://{AUTH_WEB_HOST}``, if ``AUTH_WEB_HOST`` is set, else
    - ``https://auth.{WEB_HOST}``, if ``WEB_HOST`` is set.

    Returns an empty string if none of the above resolve to a usable URL (or
    ``KEYCLOAK_REALM_NAME`` is unset).
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

    # If there is still no auth url, we try the keycloak variables
    if not auth_url:
        auth_url = os.getenv('KEYCLOAK_SERVER_URL_EXT', '')
    if not auth_url:
        auth_url = os.getenv('KEYCLOAK_SERVER_URL', '')

    kc_realm = os.getenv('KEYCLOAK_REALM_NAME', '').strip()
    if not auth_url or not kc_realm:
        return ''
    return f'{auth_url}/realms/{kc_realm}'


def _keycloak_details_from_env() -> dict | None:
    """Resolve Keycloak IDP OAuth details straight from the environment.

    Fallback for hosts that never had a generic Keycloak ``auth_providers``
    row to begin with (fresh installs, since migration 168 no longer seeds
    one -- see this migration's module docstring). Mirrors migration 168's
    removed ``_seed_from_environment`` Keycloak block. Returns ``None`` when
    ``KEYCLOAK_CLIENT_ID`` is unset -- nothing to base the per-brand rows on.
    """
    kc_client_id = os.getenv('KEYCLOAK_CLIENT_ID', '').strip()
    if not kc_client_id:
        return None

    from storage.encrypt_utils import encrypt_value

    kc_client_secret = os.getenv('KEYCLOAK_CLIENT_SECRET', '').strip()
    kc_base = _resolve_keycloak_realm_base_url()
    return {
        'kind': _OAUTH_KIND,
        'provider_type': _ENTERPRISE_SSO_PROVIDER_TYPE,
        'client_id': kc_client_id,
        'client_secret': encrypt_value(kc_client_secret) if kc_client_secret else None,
        'authorization_url': (
            f'{kc_base}/protocol/openid-connect/auth' if kc_base else None
        ),
        'token_url': (f'{kc_base}/protocol/openid-connect/token' if kc_base else None),
        'userinfo_url': (
            f'{kc_base}/protocol/openid-connect/userinfo' if kc_base else None
        ),
        'scopes': ['openid', 'email', 'profile'],
        'permitted_drift_seconds': 60,
    }


def _delete_existing_keycloak_row(bind) -> None:
    """Delete the legacy generic Keycloak row seeded by an older version of
    migration 168, if this database still has one. Matched narrowly
    (``title`` + ``provider_type``) so an admin-added second enterprise-SSO
    connector is never mistaken for it. A no-op if there is no such row."""
    bind.execute(
        sa.text(
            """
            DELETE FROM auth_providers
            WHERE kind = :kind AND is_idp = true AND title = :title
              AND details ->> 'provider_type' = :provider_type
            """
        ),
        {
            'kind': _OAUTH_KIND,
            'title': _KEYCLOAK_TITLE,
            'provider_type': _ENTERPRISE_SSO_PROVIDER_TYPE,
        },
    )


def _federated_idp_row_exists(bind, provider_type: str) -> bool:
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
            {'kind': _OAUTH_KIND, 'provider_type': provider_type},
        ).scalar_one_or_none()
        is not None
    )


def _with_idp_hint(authorization_url: str | None, provider_type: str) -> str | None:
    """Append ``kc_idp_hint=<provider_type>`` to a Keycloak authorization URL.

    Mirrors ``frontend/src/utils/generate-auth-url.ts``'s client-side
    ``kc_idp_hint`` param -- baked into the stored URL here instead, since
    ``server/routes/oauth_v2.py``'s ``oauth_v2_login`` only ever appends its
    own ``client_id``/``redirect_uri``/``response_type``/``state``/``scope``
    params (after whatever is already on ``authorization_url``), never an
    IDP-specific hint of its own.
    """
    if not authorization_url:
        return authorization_url
    separator = '&' if '?' in authorization_url else '?'
    return f'{authorization_url}{separator}kc_idp_hint={provider_type}'


def _seed_federated_idp_rows(bind, keycloak_details: dict) -> None:
    for provider_type, title, color, icon, env_var in _FEDERATED_BRANDS:
        if not os.getenv(env_var, '').strip():
            continue
        if _federated_idp_row_exists(bind, provider_type):
            continue

        details = dict(keycloak_details)
        details['authorization_url'] = _with_idp_hint(
            details.get('authorization_url'), provider_type
        )
        # Marks this row's token_url/credentials as Keycloak's own, not the
        # brand's -- see ``OAuthProviderDetails.legacy_keycloak_type``. The
        # git-provider row this brand's real tokens get stored against is
        # seeded separately (migration 168), gated on the same env var
        # checked above.
        details['legacy_keycloak_type'] = provider_type

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
                'title': title,
                'icon': icon,
                'color': color,
                'details': json.dumps(details),
            },
        )


def _rebrand_password_login(bind) -> None:
    """Repair a pre-fix integrated/password IDP row's ``title``/``color``.

    Narrowly matched on the exact pre-fix defaults so an admin who already
    retitled/recolored this row (via the admin API) is left untouched.
    """
    bind.execute(
        sa.text(
            """
            UPDATE auth_providers
            SET title = :new_title, color = :new_color,
                updated_at = CURRENT_TIMESTAMP
            WHERE kind = :kind AND title = :old_title AND color = :old_color
            """
        ),
        {
            'kind': _PASSWORD_KIND,
            'new_title': _PASSWORD_LOGIN_NEW_TITLE,
            'new_color': _PASSWORD_LOGIN_NEW_COLOR,
            'old_title': _PASSWORD_LOGIN_OLD_TITLE,
            'old_color': _PASSWORD_LOGIN_OLD_COLOR,
        },
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        raise RuntimeError(f'Unsupported database dialect: {bind.dialect.name}')

    inspector = sa.inspect(bind)
    if not inspector.has_table('auth_providers'):
        # Should not happen (migration 168 creates it) but guards against
        # running this out of order / on a fresh install that never had it.
        return

    keycloak_details = _keycloak_details_from_env()
    if keycloak_details is not None:
        _seed_federated_idp_rows(bind, keycloak_details)

    _delete_existing_keycloak_row(bind)
    _rebrand_password_login(bind)


def downgrade() -> None:
    bind = op.get_bind()

    # Revert the password-login rebrand -- same conservative matching, now
    # keyed on the *new* values so a row an admin customized away from them
    # in the meantime is left alone.
    bind.execute(
        sa.text(
            """
            UPDATE auth_providers
            SET title = :old_title, color = :old_color,
                updated_at = CURRENT_TIMESTAMP
            WHERE kind = :kind AND title = :new_title AND color = :new_color
            """
        ),
        {
            'kind': _PASSWORD_KIND,
            'old_title': _PASSWORD_LOGIN_OLD_TITLE,
            'old_color': _PASSWORD_LOGIN_OLD_COLOR,
            'new_title': _PASSWORD_LOGIN_NEW_TITLE,
            'new_color': _PASSWORD_LOGIN_NEW_COLOR,
        },
    )

    for provider_type, title, color, _icon, _env_var in _FEDERATED_BRANDS:
        bind.execute(
            sa.text(
                """
                DELETE FROM auth_providers
                WHERE kind = :kind AND is_idp = true AND title = :title
                  AND color = :color
                  AND details ->> 'legacy_keycloak_type' = :provider_type
                """
            ),
            {
                'kind': _OAUTH_KIND,
                'title': title,
                'color': color,
                'provider_type': provider_type,
            },
        )

    # Restore a generic Keycloak row equivalent to the one upgrade() may have
    # removed, resolved fresh from the environment (the original row, if
    # any, is gone by the time downgrade() runs) -- so an upgrade/downgrade
    # cycle round-trips back to "has a Keycloak row" rather than silently
    # leaving a downgraded database with none.
    keycloak_row_exists = (
        bind.execute(
            sa.text(
                """
                SELECT 1 FROM auth_providers
                WHERE kind = :kind AND is_idp = true AND title = :title
                  AND details ->> 'provider_type' = :provider_type
                LIMIT 1
                """
            ),
            {
                'kind': _OAUTH_KIND,
                'title': _KEYCLOAK_TITLE,
                'provider_type': _ENTERPRISE_SSO_PROVIDER_TYPE,
            },
        ).scalar_one_or_none()
        is not None
    )
    if not keycloak_row_exists:
        keycloak_details = _keycloak_details_from_env()
        if keycloak_details is not None:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO auth_providers
                        (kind, title, is_idp, color, allow_registration,
                         details, created_at, updated_at)
                    VALUES
                        (:kind, :title, true, :color, true, :details,
                         CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """
                ),
                {
                    'kind': _OAUTH_KIND,
                    'title': _KEYCLOAK_TITLE,
                    'color': '#000000',
                    'details': json.dumps(keycloak_details),
                },
            )
