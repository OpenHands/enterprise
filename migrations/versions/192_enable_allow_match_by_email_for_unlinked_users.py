"""Enable ``allow_match_by_email`` for existing users with no linked identity.

Switching login from the v1 (Keycloak) flow to the v2 flow gives every user a
new external ``sub``. ``resolve_or_create_user`` only reuses an existing user
for an unknown ``sub`` when that user's ``allow_match_by_email`` is true;
otherwise it mints a duplicate user. Pre-existing users have the column at
its ``false`` default, so their first v2 login created a second account.

This sets the flag on every user whose stored email is verified and who has no
``auth_provider_users`` row yet. The flag self-clears on the first successful
email link, so each user's email-match window is one login wide.

Only ``email_verified = true`` users qualify. The v1 login creates the ``user``
row before email verification is enforced, so ``email`` can be one nobody has
proven they own (``email_verified`` false or NULL). Linking such a row to
whoever later logs in via v2 with that address would hand them an account
someone else registered. Those users get a fresh account on their v2 login
instead. Users that already have a link resolve by ``sub`` and never need the
match.

Revision ID: 192
Revises: 191
Create Date: 2026-10-19 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '192'
down_revision: str | None = '191'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            """
            UPDATE "user"
            SET allow_match_by_email = true
            WHERE allow_match_by_email = false
              AND email_verified = true
              AND NOT EXISTS (
                  SELECT 1 FROM auth_provider_users apu
                  WHERE apu.user_id = "user".id
              )
            """
        )
    )


def downgrade() -> None:
    # The rows this changed are indistinguishable from users enabled by an
    # operator through the super-admin endpoint, so there is nothing safe to
    # revert.
    pass
