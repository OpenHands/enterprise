"""Add ``icon``, ``color``, and ``allow_registration`` to ``auth_providers``.

Closes a functional gap between the legacy (Keycloak-era) login page and the
OAuth v2 login path: the legacy ``/login`` screen always showed a
hard-coded button per well-known git provider brand, while the v2 path
(``GET /oauth/idp-login``) simply redirects to whichever IDP row is
"default" with no chooser at all -- there was no way for an admin-configured
IDP (e.g. a second SAML/OIDC tenant) to carry its own branding for a
"Let's Get Started" style screen populated from the database.

- ``icon`` -- raw SVG markup for the provider's login button, same shape as
  ``InstanceSettings.logo`` (a client-supplied string blob, not validated
  server-side). ``NULL`` when unset; the frontend falls back to a generic
  icon.
- ``color`` -- the button's brand color as a CSS color string. Defaults to
  black (``#000000``) rather than ``NULL`` since every row needs *some*
  color to render with even before an admin customizes it, except the
  built-in integrated/password IDP (``kind='PasswordProviderDetails'``),
  which gets its own brand color (``#03A956``) instead of black -- there is
  always at most one such row (migrations 185/186's
  ``uq_auth_providers_one_password_provider`` guard), so this is a single
  targeted ``UPDATE`` after the column is added, not a second
  column-wide default.
- ``allow_registration`` -- whether a brand-new user may be created the
  first time someone authenticates through this row (see
  ``server.auth.provider_identity.resolve_or_create_user``), or whether only
  an already-linked ``auth_provider_users`` row may sign in through it.
  Defaults to ``True`` -- the existing, pre-migration behavior for every
  provider kind.

Revision ID: 188
Revises: 187
Create Date: 2026-10-14 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '188'
down_revision: str | None = '187'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors storage.auth_provider.DEFAULT_AUTH_PROVIDER_COLOR -- duplicated
# (not imported) so this migration's data shape stays fixed in time even if
# that default changes later, same convention as other migrations in this
# directory.
_DEFAULT_COLOR = '#000000'

# The integrated/password IDP's (``server.routes.idp``) own brand color --
# see the module docstring for why this one kind gets a dedicated ``UPDATE``
# instead of the blanket ``_DEFAULT_COLOR`` every other row backfills to.
_PASSWORD_KIND = 'PasswordProviderDetails'
_PASSWORD_LOGIN_COLOR = '#03A956'


def upgrade() -> None:
    op.add_column('auth_providers', sa.Column('icon', sa.Text(), nullable=True))
    op.add_column(
        'auth_providers',
        sa.Column(
            'color',
            sa.String(32),
            nullable=False,
            server_default=_DEFAULT_COLOR,
        ),
    )
    op.add_column(
        'auth_providers',
        sa.Column(
            'allow_registration',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('true'),
        ),
    )

    bind = op.get_bind()
    bind.execute(
        sa.text('UPDATE auth_providers SET color = :color WHERE kind = :kind'),
        {'color': _PASSWORD_LOGIN_COLOR, 'kind': _PASSWORD_KIND},
    )


def downgrade() -> None:
    op.drop_column('auth_providers', 'allow_registration')
    op.drop_column('auth_providers', 'color')
    op.drop_column('auth_providers', 'icon')
