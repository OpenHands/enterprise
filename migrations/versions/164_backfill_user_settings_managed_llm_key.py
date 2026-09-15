"""Backfill user_settings.llm_api_key from the personal-org managed key.

Revision ID: 164
Revises: 163
Create Date: 2026-09-15 00:00:00.000000

The managed-key ownership repair (revision 161 / PR #353) rotated members'
managed LiteLLM keys, writing the new value onto ``org_member._llm_api_key``
but leaving the legacy ``user_settings.llm_api_key`` cache pointing at the
old (now deleted) key. The Canvas settings surface still reads that cache,
so affected users saw a dead key.

This migration re-syncs the personal-org managed key into the
``user_settings`` cache, matching what
``UserStore._sync_user_settings_from_org_member`` does at runtime:

* ``org_member._llm_api_key`` is encrypted with ``encrypt_value``.
* ``user_settings.llm_api_key`` is encrypted with ``encrypt_legacy_value``.

The two columns therefore hold different ciphertext for the same plaintext,
so the backfill must decrypt and re-encrypt through Python rather than copy
raw bytes. It is scoped to the personal org (``org_id == user_id``) because
that is the single-member org whose key the ``user_settings`` cache mirrors,
and it is idempotent: rows already in sync are skipped.
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '164'
down_revision: str | None = '163'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _decrypt(value: str | None) -> str | None:
    if not value:
        return None
    from storage.encrypt_utils import decrypt_value

    return decrypt_value(value)


def _decrypt_legacy(value: str | None) -> str | None:
    if not value:
        return None
    from storage.encrypt_utils import decrypt_legacy_value

    try:
        return decrypt_legacy_value(value)
    except Exception:
        # Unreadable legacy ciphertext is treated as "needs backfill".
        return None


def _encrypt_legacy(value: str) -> str:
    from storage.encrypt_utils import encrypt_legacy_value

    return encrypt_legacy_value(value)


def upgrade() -> None:
    bind = op.get_bind()

    org_member = sa.table(
        'org_member',
        sa.column('org_id', sa.Uuid()),
        sa.column('user_id', sa.Uuid()),
        sa.column('_llm_api_key', sa.String()),
    )
    user_settings = sa.table(
        'user_settings',
        sa.column('keycloak_user_id', sa.String()),
        sa.column('llm_api_key', sa.String()),
    )

    # Personal-org members only (org_id == user_id): that is the single-member
    # org whose managed key the user_settings cache mirrors.
    rows = bind.execute(
        sa.select(
            org_member.c.user_id,
            org_member.c._llm_api_key,
        ).where(org_member.c.org_id == org_member.c.user_id)
    ).mappings()

    for row in rows:
        new_plaintext = _decrypt(row['_llm_api_key'])
        if not new_plaintext:
            continue

        keycloak_user_id = str(row['user_id'])
        settings_row = (
            bind.execute(
                sa.select(user_settings.c.llm_api_key).where(
                    user_settings.c.keycloak_user_id == keycloak_user_id
                )
            )
            .mappings()
            .first()
        )
        if settings_row is None:
            continue

        if _decrypt_legacy(settings_row['llm_api_key']) == new_plaintext:
            continue

        bind.execute(
            user_settings.update()
            .where(user_settings.c.keycloak_user_id == keycloak_user_id)
            .values(llm_api_key=_encrypt_legacy(new_plaintext))
        )


def downgrade() -> None:
    # Data-only reconciliation: the previous cache value was a deleted,
    # unusable key, so there is nothing meaningful to restore.
    pass
