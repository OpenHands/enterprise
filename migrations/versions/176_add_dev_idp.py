"""Add ``user.password_hash`` for the dev IDP password login (OHE-3381).

The dev IDP (``server.routes.dev_idp``) is a self-hosted-only email +
password login that works without any external OAuth/OIDC provider. It is
modeled as an in-memory sentinel (``DevIdpProvider``) rather than a row in
``oauth_providers``, so this migration only adds the ``password_hash``
column used to store the Argon2id hash of a dev IDP account's password.
``NULL`` for every user who authenticates via a real IDP.

Whether the dev IDP is *usable* is a runtime decision
(``is_dev_idp_available()`` — ``INTEGRATED_IDP_ENABLED`` env var is set and no
real IDP configured), re-checked on every request.

Revision ID: 176
Revises: 175
Create Date: 2026-10-05 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '176'
down_revision: str | None = '175'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'user',
        sa.Column('password_hash', sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('user', 'password_hash')
