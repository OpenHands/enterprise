"""Add ``user.password_hash`` and seed the dev IDP provider row (OHE-3381).

The dev IDP (``server.routes.dev_idp``) is a self-hosted-only email +
password login that works without any external OAuth/OIDC provider. Unlike
the real providers seeded by migration 168, the dev IDP is **not** gated by
an environment variable at migration time: its row always exists, on every
deployment (including cloud). Whether it is actually *usable* is a runtime
decision (``is_dev_idp_available()`` — ``DEPLOYMENT_MODE == 'self_hosted'``
and no other real IDP configured), re-checked on every request. Seeding the
row unconditionally keeps that the single place the gate is enforced, rather
than also depending on the environment happening to look the same way at
migration time as it does when the app later serves requests.

Two changes:

- ``user.password_hash`` — Argon2id hash of a dev IDP account's password
  (``server.auth.password_hashing``). ``NULL`` for every user who
  authenticates via a real IDP.
- One ``oauth_providers`` row: ``provider_category = 'dev_idp'``,
  ``is_idp = True``, no authorization/token/userinfo URL (it never talks to
  an external endpoint) and no client secret.

Revision ID: 175
Revises: 174
Create Date: 2026-10-05 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '175'
down_revision: str | None = '174'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Keep in sync with ``storage.oauth_provider.DEV_IDP_CATEGORY``. Migrations
# intentionally don't import application modules (see migration 168), so
# this is a second, deliberately-duplicated copy of the constant.
_DEV_IDP_CATEGORY = 'dev_idp'


def upgrade() -> None:
    op.add_column(
        'user',
        sa.Column('password_hash', sa.String(), nullable=True),
    )

    bind = op.get_bind()
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
            'provider_category': _DEV_IDP_CATEGORY,
            'display_name': 'Development Login',
            'client_id': 'dev-idp',
        },
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text('DELETE FROM oauth_providers WHERE provider_category = :category'),
        {'category': _DEV_IDP_CATEGORY},
    )
    op.drop_column('user', 'password_hash')
