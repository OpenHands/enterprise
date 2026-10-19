"""Enforce at most one integrated/password IDP row, and that it is an IDP.

``storage.oauth_provider.INTEGRATED_IDP_CATEGORY`` (``'integrated_idp'``)
identifies the built-in email+password login IDP (``server.routes.idp``).
There is no reason for more than one such row to exist -- the application
code always resolves "the" integrated IDP via
``OAuthProviderStore.get_first_by_category`` (lowest ``id`` wins), so a
second row would simply be a dead, admin-visible duplicate -- and no reason
for it to ever be anything other than ``is_idp=True``: ``oauth_v2.py``
intercepts ``/oauth/{id}/login`` and ``/callback`` by
``provider_category == INTEGRATED_IDP_CATEGORY`` alone, while
``get_idp_providers()`` / ``get_first_idp()`` / ``_has_real_idp()`` key off
``is_idp`` alone -- the two must never disagree, or the row becomes
reachable through one code path and invisible (or miscategorized as a git
provider) through the other.

Both invariants were already implicit in migration 179 (which refuses to
seed a second row) and in every runtime code path, but nothing stopped the
``oauth_providers`` admin CRUD API (added after migration 179) from creating
a second row or flipping ``is_idp`` on the existing one. This migration adds
DB-level guards so neither the admin API nor any future code path can violate
them silently:

* A partial unique index on ``provider_category`` restricted to
  ``provider_category = 'integrated_idp'`` -- since every row matching that
  predicate has the same ``provider_category`` value, the uniqueness
  constraint caps the predicate to at most one row.
* A ``CHECK`` constraint requiring ``is_idp`` to be ``true`` whenever
  ``provider_category = 'integrated_idp'``.

This is a pre-release product; no existing deployment is expected to violate
either invariant (migration 179 already enforces "at most one" at seed time,
and seeds ``is_idp=True`` unconditionally), so this migration does not
attempt to repair pre-existing violations before adding the constraints.

Revision ID: 185
Revises: 184
Create Date: 2026-10-12 00:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '185'
down_revision: str | None = '184'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIQUE_INDEX_NAME = 'uq_oauth_providers_one_integrated_idp'
CHECK_CONSTRAINT_NAME = 'ck_oauth_providers_integrated_idp_is_idp'
_INTEGRATED_IDP_CATEGORY = 'integrated_idp'


def upgrade() -> None:
    op.create_index(
        UNIQUE_INDEX_NAME,
        'oauth_providers',
        ['provider_category'],
        unique=True,
        postgresql_where=sa.text(f"provider_category = '{_INTEGRATED_IDP_CATEGORY}'"),
    )
    op.create_check_constraint(
        CHECK_CONSTRAINT_NAME,
        'oauth_providers',
        f"provider_category <> '{_INTEGRATED_IDP_CATEGORY}' OR is_idp",
    )


def downgrade() -> None:
    op.drop_constraint(CHECK_CONSTRAINT_NAME, 'oauth_providers', type_='check')
    op.drop_index(UNIQUE_INDEX_NAME, table_name='oauth_providers')
