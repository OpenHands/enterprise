"""Add permission profiles.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-09
"""

from __future__ import annotations

from alembic import op


revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS permission_profiles (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            name TEXT,
            snapshot JSONB NOT NULL DEFAULT '{"integrations": {}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """)
    op.execute("DROP INDEX IF EXISTS permission_profiles_owner_name_idx")
    op.execute("ALTER TABLE permission_profiles ALTER COLUMN name DROP NOT NULL")

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS permission_profiles_owner_default_idx
        ON permission_profiles (LOWER(owner_id))
        WHERE name IS NULL
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS permission_profiles_owner_name_idx
        ON permission_profiles (LOWER(owner_id), LOWER(name))
        WHERE name IS NOT NULL
    """)
    op.execute("""
        INSERT INTO permission_profiles (id, owner_id, name, snapshot, created_at, updated_at)
        SELECT 'default:' || md5(LOWER(owner_id)), owner_id, NULL,
               '{"integrations": {}}'::jsonb, created_at, updated_at
        FROM agent_api_keys
        ON CONFLICT DO NOTHING
    """)
    op.execute("ALTER TABLE agent_api_keys ADD COLUMN IF NOT EXISTS profile_id TEXT")
    op.execute("""
        UPDATE agent_api_keys AS keys
        SET profile_id = profiles.id
        FROM permission_profiles AS profiles
        WHERE keys.profile_id IS NULL
          AND LOWER(keys.owner_id) = LOWER(profiles.owner_id)
          AND profiles.name IS NULL
    """)
    op.execute(
        "ALTER TABLE agent_api_keys DROP CONSTRAINT IF EXISTS agent_api_keys_pkey"
    )
    op.execute("""
        DELETE FROM agent_api_keys
        WHERE profile_id IS NULL
    """)
    op.execute("ALTER TABLE agent_api_keys ALTER COLUMN profile_id SET NOT NULL")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS agent_api_keys_profile_id_idx
        ON agent_api_keys (profile_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS agent_api_keys_profile_id_idx")
    op.execute("ALTER TABLE agent_api_keys DROP COLUMN IF EXISTS profile_id")
    op.execute("DROP INDEX IF EXISTS permission_profiles_owner_name_idx")
    op.execute("DROP INDEX IF EXISTS permission_profiles_owner_default_idx")
    op.execute("DROP TABLE IF EXISTS permission_profiles")
