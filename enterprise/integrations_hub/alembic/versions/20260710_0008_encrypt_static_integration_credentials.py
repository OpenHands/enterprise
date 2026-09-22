"""Encrypt static integration credentials and OAuth PKCE verifiers at rest.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-10
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from alembic import op


revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None


def _encrypt_credentials(credentials: dict[str, Any]) -> str:
    try:
        from app.crypto import encrypt_credentials
    except ModuleNotFoundError:
        from integrations_hub.crypto import encrypt_credentials

    return encrypt_credentials(credentials)


def _decrypt_credentials(payload: str) -> dict[str, Any]:
    try:
        from app.crypto import decrypt_credentials
    except ModuleNotFoundError:
        from integrations_hub.crypto import decrypt_credentials

    return decrypt_credentials(payload)


def _backfill_legacy_credentials() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        text("""
            SELECT owner_id, key, credentials
            FROM owner_integrations
            WHERE encrypted_credentials IS NULL
              AND credentials <> '{}'::jsonb
        """)
    ).mappings()
    for row in rows:
        credentials = row.get("credentials")
        if not isinstance(credentials, dict):
            continue
        connection.execute(
            text("""
                UPDATE owner_integrations
                SET credentials = '{}'::jsonb,
                    encrypted_credentials = :encrypted_credentials,
                    updated_at = NOW()
                WHERE owner_id = :owner_id AND key = :key
            """),
            {
                "owner_id": row["owner_id"],
                "key": row["key"],
                "encrypted_credentials": _encrypt_credentials(credentials),
            },
        )


def _restore_legacy_credentials() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        text("""
            SELECT owner_id, key, encrypted_credentials
            FROM owner_integrations
            WHERE encrypted_credentials IS NOT NULL
        """)
    ).mappings()
    for row in rows:
        credentials = _decrypt_credentials(str(row["encrypted_credentials"]))
        connection.execute(
            text("""
                UPDATE owner_integrations
                SET credentials = CAST(:credentials AS jsonb),
                    encrypted_credentials = NULL,
                    updated_at = NOW()
                WHERE owner_id = :owner_id AND key = :key
            """),
            {
                "owner_id": row["owner_id"],
                "key": row["key"],
                "credentials": json.dumps(credentials, separators=(",", ":")),
            },
        )


def _encrypt_verifier(verifier: str) -> str:
    return _encrypt_credentials({"code_verifier": verifier})


def _decrypt_verifier(payload: str) -> str:
    data = _decrypt_credentials(payload)
    value = data.get("code_verifier")
    return str(value) if isinstance(value, str) else ""


def _backfill_legacy_code_verifiers() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        text("""
            SELECT state, code_verifier
            FROM oauth_states
            WHERE encrypted_code_verifier IS NULL
              AND code_verifier IS NOT NULL
              AND code_verifier <> ''
        """)
    ).mappings()
    for row in rows:
        verifier = str(row["code_verifier"])
        if not verifier:
            continue
        connection.execute(
            text("""
                UPDATE oauth_states
                SET code_verifier = NULL,
                    encrypted_code_verifier = :encrypted_code_verifier
                WHERE state = :state
            """),
            {
                "state": row["state"],
                "encrypted_code_verifier": _encrypt_verifier(verifier),
            },
        )


def _restore_legacy_code_verifiers() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        text("""
            SELECT state, encrypted_code_verifier
            FROM oauth_states
            WHERE encrypted_code_verifier IS NOT NULL
        """)
    ).mappings()
    for row in rows:
        connection.execute(
            text("""
                UPDATE oauth_states
                SET code_verifier = :code_verifier,
                    encrypted_code_verifier = NULL
                WHERE state = :state
            """),
            {
                "state": row["state"],
                "code_verifier": _decrypt_verifier(str(row["encrypted_code_verifier"])),
            },
        )


def upgrade() -> None:
    op.execute(
        "ALTER TABLE owner_integrations ADD COLUMN IF NOT EXISTS encrypted_credentials TEXT"
    )
    _backfill_legacy_credentials()
    op.execute(
        "ALTER TABLE oauth_states ADD COLUMN IF NOT EXISTS encrypted_code_verifier TEXT"
    )
    _backfill_legacy_code_verifiers()


def downgrade() -> None:
    _restore_legacy_code_verifiers()
    op.execute("ALTER TABLE oauth_states DROP COLUMN IF EXISTS encrypted_code_verifier")
    _restore_legacy_credentials()
    op.execute(
        "ALTER TABLE owner_integrations DROP COLUMN IF EXISTS encrypted_credentials"
    )
