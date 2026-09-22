"""Add indexes for repository query patterns.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-12
"""

from __future__ import annotations

from alembic import op


revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Repository lookups normalize owner IDs with LOWER(), so the plain primary
    # keys on (owner_id, key) cannot support these predicates.
    op.execute("""
        CREATE INDEX IF NOT EXISTS owner_integrations_owner_key_lower_idx
        ON owner_integrations (LOWER(owner_id), key)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS integration_connections_owner_key_lower_idx
        ON integration_connections (LOWER(owner_id), integration_key)
    """)

    # Service-level connector deletion removes every owner's saved connection.
    op.execute("""
        CREATE INDEX IF NOT EXISTS integration_connections_integration_key_idx
        ON integration_connections (integration_key)
    """)

    # Owner approval pages are newest-first, while automatic request
    # de-duplication narrows pending requests by this complete key.
    op.execute("""
        CREATE INDEX IF NOT EXISTS access_requests_owner_created_at_idx
        ON access_requests (
            LOWER(COALESCE(notification_targets->>'in_app', '')),
            created_at DESC
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS access_requests_pending_lookup_idx
        ON access_requests (
            LOWER(COALESCE(notification_targets->>'in_app', '')),
            COALESCE(agent_id, ''),
            COALESCE(agent_class, ''),
            integration_key,
            function_name,
            created_at DESC
        )
        WHERE status = 'pending'
    """)

    # Invocation history grows on every tool call. This index bounds the rows
    # read for one owner's grouped usage summary and supplies its time ordering.
    op.execute("""
        CREATE INDEX IF NOT EXISTS tool_invocations_owner_tool_invoked_at_idx
        ON tool_invocations (
            LOWER(owner_id),
            integration_key,
            function_name,
            invoked_at DESC
        )
        INCLUDE (outcome)
    """)

    # The expiry cron previously scanned the entire grants table.
    op.execute("""
        CREATE INDEX IF NOT EXISTS temporary_grants_expires_at_idx
        ON temporary_grants (expires_at)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS temporary_grants_expires_at_idx")
    op.execute("DROP INDEX IF EXISTS tool_invocations_owner_tool_invoked_at_idx")
    op.execute("DROP INDEX IF EXISTS access_requests_pending_lookup_idx")
    op.execute("DROP INDEX IF EXISTS access_requests_owner_created_at_idx")
    op.execute("DROP INDEX IF EXISTS integration_connections_integration_key_idx")
    op.execute("DROP INDEX IF EXISTS integration_connections_owner_key_lower_idx")
    op.execute("DROP INDEX IF EXISTS owner_integrations_owner_key_lower_idx")
