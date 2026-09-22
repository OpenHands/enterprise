"""Add stable connection IDs and external resource bindings.

Revision ID: 0009
Revises: 0008
"""

from alembic import op


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE managed_connectors ADD COLUMN IF NOT EXISTS connection_model JSONB"
    )
    op.execute(
        "ALTER TABLE integration_connections ADD COLUMN IF NOT EXISTS connection_id TEXT"
    )
    op.execute(
        """
        UPDATE integration_connections
        SET connection_id = 'conn_' || md5(LOWER(owner_id) || ':' || integration_key)
        WHERE connection_id IS NULL OR connection_id = ''
        """
    )
    op.execute(
        "ALTER TABLE integration_connections ALTER COLUMN connection_id SET NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS integration_connections_connection_id_idx ON integration_connections (connection_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS integration_connections_owner_provider_idx ON integration_connections (LOWER(owner_id), provider)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS connection_resources (
            id TEXT PRIMARY KEY,
            connection_id TEXT NOT NULL REFERENCES integration_connections(connection_id) ON DELETE CASCADE,
            owner_id TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            external_resource_id TEXT NOT NULL,
            display_name TEXT,
            external_url TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            UNIQUE (connection_id, resource_type, external_resource_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS connection_resources_owner_idx ON connection_resources (LOWER(owner_id), connection_id)"
    )
    op.execute(
        "ALTER TABLE tool_invocations ADD COLUMN IF NOT EXISTS connection_id TEXT"
    )
    op.execute("ALTER TABLE tool_invocations ADD COLUMN IF NOT EXISTS resource_id TEXT")
    op.execute(
        "CREATE INDEX IF NOT EXISTS tool_invocations_connection_resource_idx ON tool_invocations (connection_id, resource_id)"
    )
    op.execute(
        """
        INSERT INTO connection_resources (
            id, connection_id, owner_id, resource_type, external_resource_id,
            display_name, metadata, created_at, updated_at
        )
        SELECT
            'res_' || md5(connection_id || ':' || external_workspace_id),
            connection_id,
            owner_id,
            'workspace',
            external_workspace_id,
            display_name,
            '{}'::jsonb,
            created_at,
            updated_at
        FROM integration_connections
        WHERE external_workspace_id IS NOT NULL AND external_workspace_id <> ''
        ON CONFLICT (connection_id, resource_type, external_resource_id) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE owner_integrations AS integration
        SET config = COALESCE(integration.config, '{}'::jsonb)
            || jsonb_build_object('connectionId', connection.connection_id)
        FROM integration_connections AS connection
        WHERE LOWER(integration.owner_id) = LOWER(connection.owner_id)
          AND integration.key = connection.integration_key
          AND integration.auth_strategy = 'oauth2'
          AND NOT (COALESCE(integration.config, '{}'::jsonb) ? 'connectionId')
        """
    )
    op.execute(
        """
        UPDATE owner_integrations AS integration
        SET config = COALESCE(integration.config, '{}'::jsonb)
            || jsonb_build_object('resourceId', resource.id)
        FROM integration_connections AS connection
        JOIN connection_resources AS resource
          ON resource.connection_id = connection.connection_id
        WHERE LOWER(integration.owner_id) = LOWER(connection.owner_id)
          AND integration.key = connection.integration_key
          AND integration.auth_strategy = 'oauth2'
          AND NOT (COALESCE(integration.config, '{}'::jsonb) ? 'resourceId')
          AND (
              SELECT COUNT(*)
              FROM connection_resources AS candidate
              WHERE candidate.connection_id = connection.connection_id
          ) = 1
        """
    )


def downgrade() -> None:
    op.execute(
        "UPDATE owner_integrations SET config = COALESCE(config, '{}'::jsonb) - 'connectionId' - 'resourceId'"
    )
    op.execute("DROP INDEX IF EXISTS tool_invocations_connection_resource_idx")
    op.execute("ALTER TABLE tool_invocations DROP COLUMN IF EXISTS resource_id")
    op.execute("ALTER TABLE tool_invocations DROP COLUMN IF EXISTS connection_id")
    op.execute("DROP TABLE IF EXISTS connection_resources")
    op.execute("DROP INDEX IF EXISTS integration_connections_owner_provider_idx")
    op.execute("DROP INDEX IF EXISTS integration_connections_connection_id_idx")
    op.execute(
        "ALTER TABLE integration_connections DROP COLUMN IF EXISTS connection_id"
    )
    op.execute("ALTER TABLE managed_connectors DROP COLUMN IF EXISTS connection_model")
