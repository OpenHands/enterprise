"""Add ``enabled`` to ``auth_providers``.

An admin could create a provider row -- including from a preset, which seeds
placeholder client IDs and URLs -- and it immediately appeared on the login
screen and accepted sign-ins, with no way to stage a half-configured row.
``enabled`` adds that switch: a row is only usable for a *new* login/link when
this is true, and the admin API lets a super admin flip it.

New rows default to ``false`` so a freshly created (usually still
unconfigured) provider is inert until the admin enables it. Rows that already
exist were all live before this column existed, so they are backfilled to
``true`` -- otherwise upgrading would silently disable every configured login
IDP and lock every user out.

Disabling a row deliberately does *not* invalidate sessions or tokens already
minted through it: ``enabled`` is checked when starting a flow
(``GET /oauth/{id}/login``, ``POST /oauth/{id}/link``, ``/saml/{id}/login``,
``/oauth/idp-login``, the password IDP's routes), when finishing one
(``/oauth/{id}/callback``, ``/saml/{id}/acs``), and when listing providers for
the login/integrations UI, not when resolving an existing ``openhands_auth``
cookie or refreshing its tokens. Flipping the flag is therefore reversible
without a sign-out.

Revision ID: 196
Revises: 195
Create Date: 2026-10-22 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '196'
down_revision: str | None = '195'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'auth_providers',
        sa.Column(
            'enabled',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )
    # Every pre-existing row was reachable before this column existed.
    op.execute(sa.text('UPDATE auth_providers SET enabled = true'))


def downgrade() -> None:
    op.drop_column('auth_providers', 'enabled')
