"""Repair the Keycloak IDP row's URLs seeded by migration 168 (OHE-3293/3294).

Migration 168 seeded the ``oauth_providers`` Keycloak IDP row's
``authorization_url`` / ``token_url`` / ``userinfo_url`` from
``KEYCLOAK_SERVER_URL``. That variable is the in-cluster address the backend
uses for server-to-server Keycloak admin calls — it is not reachable by end
users' browsers (or, for ``token_url`` / ``userinfo_url``, necessarily correct
for direct server-to-IDP calls either, once a reverse proxy / ingress is
involved). On any deployment where the in-cluster and externally-reachable
Keycloak hostnames differ (e.g. staging, where ``KEYCLOAK_SERVER_URL`` is
``http://keycloak.keycloak``), the seeded row ends up unusable.

168 has since been fixed to derive these URLs from the externally-reachable
hostname instead:

1. ``AUTH_URL``, if set, else
2. ``https://{AUTH_WEB_HOST}``, if ``AUTH_WEB_HOST`` is set, else
3. ``https://auth.{WEB_HOST}``, if ``WEB_HOST`` is set.

combined with ``KEYCLOAK_REALM_NAME`` into
``{auth_base_url}/realms/{KEYCLOAK_REALM_NAME}/protocol/openid-connect/{auth,token,userinfo}``.

This migration repairs hosts that already ran 168 before that fix landed, by
recomputing the same three URLs for any row whose ``authorization_url``
starts with the known-broken in-cluster prefix (``http://keycloak.keycloak``)
from the current environment. Matching on the broken value itself (rather
than ``provider_category``/``is_idp``) targets exactly the rows the bug
produced and leaves any row an operator may already have corrected alone.
There is no admin API for editing ``oauth_providers`` rows, so this is the
only way to correct an already-seeded value.

The repair only writes when the current environment resolves to a usable
auth base URL *and* ``KEYCLOAK_REALM_NAME`` is set; otherwise the existing
(possibly broken) row is left untouched rather than wiped to NULL. Hosts with
no Keycloak IDP row (OSS / git-provider-only installs) are unaffected.

Revision ID: 178
Revises: 177
Create Date: 2026-10-08 00:00:00.000000
"""

import os
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '178'
down_revision: str | None = '177'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The in-cluster Keycloak address migration 168 mistakenly used to seed
# externally-facing URLs. Any ``authorization_url`` starting with this is
# unambiguously the bug's output, not a legitimate configuration.
_BROKEN_URL_PREFIX = 'http://keycloak.keycloak'


def _resolve_keycloak_realm_base_url() -> str:
    """Resolve the externally-reachable Keycloak realm base URL.

    Mirrors the corrected derivation in migration 168: ``AUTH_URL``, falling
    back to ``AUTH_WEB_HOST``, falling back to ``auth.{WEB_HOST}``. Returns an
    empty string if no base URL resolves, or ``KEYCLOAK_REALM_NAME`` is unset.
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

    kc_realm = os.getenv('KEYCLOAK_REALM_NAME', '').strip()
    if not auth_url or not kc_realm:
        return ''
    return f'{auth_url}/realms/{kc_realm}'


def upgrade() -> None:
    kc_base = _resolve_keycloak_realm_base_url()
    if not kc_base:
        # Nothing we can safely repair to -- leave the existing row alone.
        return

    bind = op.get_bind()
    oauth_providers = sa.table(
        'oauth_providers',
        sa.column('id', sa.Integer()),
        sa.column('authorization_url', sa.String()),
        sa.column('token_url', sa.String()),
        sa.column('userinfo_url', sa.String()),
        sa.column('updated_at', sa.DateTime(timezone=True)),
    )
    bind.execute(
        oauth_providers.update()
        .where(oauth_providers.c.authorization_url.startswith(_BROKEN_URL_PREFIX))
        .values(
            authorization_url=f'{kc_base}/protocol/openid-connect/auth',
            token_url=f'{kc_base}/protocol/openid-connect/token',
            userinfo_url=f'{kc_base}/protocol/openid-connect/userinfo',
            updated_at=sa.text('CURRENT_TIMESTAMP'),
        )
    )


def downgrade() -> None:
    # The pre-repair values were broken (in-cluster-only URLs); restoring them
    # is not a safe or desirable rollback.
    pass
