"""Add hash/encrypted metadata for agent API keys.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-28
"""

from __future__ import annotations

import hashlib

from sqlalchemy import text

from alembic import op


revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def _hash_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _agent_key_storage_marker(value: str) -> str:
    return "hashed:" + _hash_key(value)[:32]


def _try_encrypt_agent_key(value: str) -> str | None:
    # Some migration environments install only Alembic dependencies; hash backfill
    # should still preserve existing key authentication if encryption is unavailable.
    try:
        try:
            from app.crypto import encrypt_api_key
        except ModuleNotFoundError:
            from integrations_hub.crypto import encrypt_api_key

        return encrypt_api_key(value)
    except Exception:
        return None


def _overwrite_existing_agent_keys() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        text("""
            SELECT profile_id, api_key
            FROM agent_api_keys
            WHERE api_key IS NOT NULL
              AND api_key NOT LIKE 'hashed:%'
        """)
    ).mappings()
    for row in rows:
        value = str(row["api_key"])
        connection.execute(
            text("""
                UPDATE agent_api_keys
                SET api_key = :api_key,
                    api_key_hash = :api_key_hash,
                    key_prefix = :key_prefix,
                    key_suffix = :key_suffix,
                    encrypted_api_key = :encrypted_api_key
                WHERE profile_id = :profile_id
            """),
            {
                "profile_id": row["profile_id"],
                "api_key": _agent_key_storage_marker(value),
                "api_key_hash": _hash_key(value),
                "key_prefix": value[:12],
                "key_suffix": value[-4:],
                "encrypted_api_key": _try_encrypt_agent_key(value),
            },
        )


def upgrade() -> None:
    op.execute("ALTER TABLE agent_api_keys ADD COLUMN IF NOT EXISTS api_key_hash TEXT")
    op.execute("ALTER TABLE agent_api_keys ADD COLUMN IF NOT EXISTS key_prefix TEXT")
    op.execute("ALTER TABLE agent_api_keys ADD COLUMN IF NOT EXISTS key_suffix TEXT")
    op.execute(
        "ALTER TABLE agent_api_keys ADD COLUMN IF NOT EXISTS encrypted_api_key TEXT"
    )
    _overwrite_existing_agent_keys()
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS agent_api_keys_api_key_hash_idx
        ON agent_api_keys (api_key_hash)
        WHERE api_key_hash IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS agent_api_keys_api_key_hash_idx")
    op.execute("ALTER TABLE agent_api_keys DROP COLUMN IF EXISTS encrypted_api_key")
    op.execute("ALTER TABLE agent_api_keys DROP COLUMN IF EXISTS key_suffix")
    op.execute("ALTER TABLE agent_api_keys DROP COLUMN IF EXISTS key_prefix")
    op.execute("ALTER TABLE agent_api_keys DROP COLUMN IF EXISTS api_key_hash")
