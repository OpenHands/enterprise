from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from secrets import token_urlsafe
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import get_default_config
from .crypto import (
    decrypt_api_key,
    decrypt_credentials,
    encrypt_api_key,
    encrypt_credentials,
)
from .models import (
    AccessRequest,
    AdminConnectionFilters,
    AdminOverview,
    AgentApiKeyRecord,
    ConnectionResource,
    ConnectionResourceInput,
    ConnectionSummary,
    DuplicateExternalAccount,
    IntegrationRequestRecord,
    IntegrationSpec,
    KeyStatus,
    ManagedConnector,
    NotificationRecord,
    OAuthConnection,
    OAuthConnectionMetadata,
    OAuthCredentials,
    OAuthState,
    PermissionProfile,
    PermissionProfileSnapshot,
    TemporaryGrant,
    ToolAccessPatch,
    ToolSpec,
    ToolUsageSummary,
    now_iso,
)


INTEGRATION_REQUEST_METADATA_TYPE = "integration_request"


def _metadata_text(metadata: dict[str, Any], key: str, default: str = "") -> str:
    value = metadata.get(key)
    if value is None:
        return default
    return str(value)


def integration_request_from_notification(
    notification: dict[str, Any],
) -> IntegrationRequestRecord | None:
    metadata = notification.get("metadata")
    if not isinstance(metadata, dict):
        return None
    if metadata.get("type") != INTEGRATION_REQUEST_METADATA_TYPE:
        return None
    source = metadata.get("source")
    if source not in {"catalog", "custom"}:
        return None
    status = metadata.get("status") or "pending"
    if status not in {"pending", "added", "dismissed"}:
        status = "pending"
    slug = _metadata_text(metadata, "slug").strip()
    return IntegrationRequestRecord(
        id=str(notification.get("id") or ""),
        source=source,
        slug=slug or None,
        name=_metadata_text(metadata, "name") or "Integration",
        description=_metadata_text(metadata, "description"),
        docsUrl=_metadata_text(metadata, "docsUrl"),
        notes=_metadata_text(metadata, "notes"),
        requestedBy=_metadata_text(metadata, "requestedBy"),
        status=status,
        decidedBy=_metadata_text(metadata, "decidedBy") or None,
        decidedAt=_metadata_text(metadata, "decidedAt") or None,
        createdAt=str(notification.get("createdAt") or now_iso()),
    )


@dataclass
class Repository:
    """Repository for integrations-hub data access.

    Schema is managed by Alembic migrations in ``integrations_hub/alembic/``
    against the shared OHE Postgres (``DB_*`` / optional ``INTHUB_POSTGRES_URL``).
    Run ``poetry run alembic -c integrations_hub/alembic.ini upgrade head``
    before starting the app with Hub enabled.
    """

    @property
    def database_url(self) -> str | None:
        return get_default_config().postgres_url

    def db_rows(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if not self.database_url:
            return []
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                if cur.description is None:
                    return []
                return list(cur.fetchall())

    def execute_db(self, query: str, params: tuple[Any, ...] = ()) -> None:
        if not self.database_url:
            return
        with psycopg.connect(self.database_url) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
            conn.commit()

    def database_ready(self) -> bool:
        """Return whether the configured Postgres database accepts a query."""
        if not self.database_url:
            return False
        try:
            return bool(self.db_rows("SELECT 1 AS ready"))
        except psycopg.Error:
            return False

    @staticmethod
    def iso(value: Any) -> str | None:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat().replace("+00:00", "Z")
        return str(value)

    @staticmethod
    def key_status(
        value: str | None, created_at: Any = None, updated_at: Any = None
    ) -> dict[str, Any] | None:
        if not value:
            return None
        return KeyStatus(
            keyPrefix=value[:12],
            keySuffix=value[-4:],
            createdAt=Repository.iso(created_at) or now_iso(),
            updatedAt=Repository.iso(updated_at) or now_iso(),
        ).model_dump()

    @staticmethod
    def stored_key_status(
        key_prefix: Any, key_suffix: Any, created_at: Any = None, updated_at: Any = None
    ) -> dict[str, Any] | None:
        prefix = str(key_prefix or "")
        suffix = str(key_suffix or "")
        if not prefix and not suffix:
            return None
        return KeyStatus(
            keyPrefix=prefix,
            keySuffix=suffix,
            createdAt=Repository.iso(created_at) or now_iso(),
            updatedAt=Repository.iso(updated_at) or now_iso(),
        ).model_dump()

    @staticmethod
    def decrypt_api_key_value(payload: str | None) -> str | None:
        try:
            return decrypt_api_key(payload)
        except Exception:
            return None

    @classmethod
    def stored_agent_key_value(cls, row: dict[str, Any]) -> str:
        encrypted = row.get("encrypted_api_key")
        return (cls.decrypt_api_key_value(str(encrypted)) or "") if encrypted else ""

    @classmethod
    def agent_key_storage_marker(cls, value: str) -> str:
        return "hashed:" + cls.hash_key(value)[:32]

    @staticmethod
    def hash_key(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    integrations: dict[str, dict[str, IntegrationSpec]] = field(default_factory=dict)

    @staticmethod
    def normalize_owner_id(value: Any) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def same_scopes(left: list[str], right: list[str]) -> bool:
        return sorted(left) == sorted(right)

    @classmethod
    def access_request_owner_id(cls, request: AccessRequest) -> str | None:
        notification_owner = cls.normalize_owner_id(
            request.notificationTargets.get("in_app")
        )
        return notification_owner or None

    @classmethod
    def access_request_matches_owner(
        cls, request: AccessRequest, owner_id: str
    ) -> bool:
        return cls.access_request_owner_id(request) == cls.normalize_owner_id(owner_id)

    @classmethod
    def owner_scoped_access_request(
        cls, owner_id: str, request: AccessRequest
    ) -> AccessRequest:
        notification_targets = dict(request.notificationTargets)
        notification_targets.setdefault("in_app", cls.normalize_owner_id(owner_id))
        channels = list(request.channels)
        if "in_app" not in channels:
            channels.append("in_app")
        return request.model_copy(
            update={"channels": channels, "notificationTargets": notification_targets}
        )

    access_requests: dict[str, dict[str, AccessRequest]] = field(default_factory=dict)
    managed_connectors: dict[str, ManagedConnector] = field(default_factory=dict)
    agent_keys: dict[str, str] = field(default_factory=dict)
    user_keys: dict[str, str] = field(default_factory=dict)
    admin_keys: dict[str, str] = field(default_factory=dict)
    oauth_states: dict[str, OAuthState] = field(default_factory=dict)
    connections: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    connection_resources: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    temporary_grants: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_invocations: list[dict[str, Any]] = field(default_factory=list)
    permission_profiles: dict[str, dict[str, PermissionProfile]] = field(
        default_factory=dict
    )
    profile_agent_keys: dict[str, str] = field(default_factory=dict)
    notifications: list[dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def legacy_credentials(row: dict[str, Any]) -> dict[str, Any]:
        credentials = row.get("credentials")
        return credentials if isinstance(credentials, dict) else {}

    @classmethod
    def integration_credentials(cls, row: dict[str, Any]) -> dict[str, Any]:
        encrypted = row.get("encrypted_credentials")
        if encrypted:
            return decrypt_credentials(str(encrypted))
        return cls.legacy_credentials(row)

    def row_to_integration(
        self, row: dict[str, Any], *, include_credentials: bool = True
    ) -> IntegrationSpec:
        return IntegrationSpec(
            key=str(row["key"]),
            name=str(row["name"]),
            kind=row["kind"],
            provider=row["provider"],
            authStrategy=row["auth_strategy"],
            enabled=bool(row["enabled"]),
            fineGrainedPermissions=bool(row["fine_grained_permissions"]),
            config=row.get("config") or {},
            credentials=self.integration_credentials(row)
            if include_credentials
            else {},
            tools=row.get("functions") or {},
        )

    def list_integrations(self, owner_id: str) -> list[IntegrationSpec]:
        if self.database_url:
            rows = self.db_rows(
                """
                SELECT * FROM owner_integrations
                WHERE LOWER(owner_id) = LOWER(%s)
                ORDER BY key ASC
                """,
                (owner_id,),
            )
            return [
                self.row_to_integration(row, include_credentials=False).redacted()
                for row in rows
            ]
        return [
            integration.redacted()
            for integration in self.integrations.get(owner_id, {}).values()
        ]

    def save_integration(
        self, owner_id: str, integration: IntegrationSpec
    ) -> IntegrationSpec:
        if self.database_url:
            encrypted_credentials = (
                encrypt_credentials(integration.credentials)
                if integration.credentials
                else None
            )
            self.execute_db(
                """
                INSERT INTO owner_integrations (
                    owner_id, key, name, kind, provider, auth_strategy, enabled,
                    fine_grained_permissions, config, credentials, encrypted_credentials,
                    functions, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (owner_id, key) DO UPDATE SET
                    name = EXCLUDED.name,
                    kind = EXCLUDED.kind,
                    provider = EXCLUDED.provider,
                    auth_strategy = EXCLUDED.auth_strategy,
                    enabled = EXCLUDED.enabled,
                    fine_grained_permissions = EXCLUDED.fine_grained_permissions,
                    config = EXCLUDED.config,
                    credentials = EXCLUDED.credentials,
                    encrypted_credentials = EXCLUDED.encrypted_credentials,
                    functions = EXCLUDED.functions,
                    updated_at = NOW()
                """,
                (
                    owner_id,
                    integration.key,
                    integration.name,
                    integration.kind,
                    integration.provider,
                    integration.authStrategy,
                    integration.enabled,
                    integration.fineGrainedPermissions,
                    Jsonb(integration.config),
                    Jsonb({}),
                    encrypted_credentials,
                    Jsonb(
                        {
                            name: tool.model_dump()
                            for name, tool in integration.tools.items()
                        }
                    ),
                ),
            )
            return integration.redacted()
        self.integrations.setdefault(owner_id, {})[integration.key] = integration
        return integration.redacted()

    def get_integration(
        self, owner_id: str, integration_key: str
    ) -> IntegrationSpec | None:
        if self.database_url:
            rows = self.db_rows(
                """
                SELECT * FROM owner_integrations
                WHERE LOWER(owner_id) = LOWER(%s) AND key = %s
                """,
                (owner_id, integration_key),
            )
            if not rows:
                return None
            row = rows[0]
            integration = self.row_to_integration(row)
            if not row.get("encrypted_credentials") and integration.credentials:
                self.backfill_legacy_integration_credentials(
                    owner_id, integration_key, integration.credentials
                )
            return integration
        return self.integrations.get(owner_id, {}).get(integration_key)

    def backfill_legacy_integration_credentials(
        self, owner_id: str, integration_key: str, credentials: dict[str, Any]
    ) -> None:
        self.execute_db(
            """
            UPDATE owner_integrations
            SET credentials = %s, encrypted_credentials = %s, updated_at = NOW()
            WHERE LOWER(owner_id) = LOWER(%s) AND key = %s
            """,
            (Jsonb({}), encrypt_credentials(credentials), owner_id, integration_key),
        )

    def delete_integration(self, owner_id: str, integration_key: str) -> bool:
        if self.database_url:
            rows = self.db_rows(
                """
                WITH deleted_connections AS (
                    DELETE FROM integration_connections
                    WHERE LOWER(owner_id) = LOWER(%s) AND integration_key = %s
                )
                DELETE FROM owner_integrations
                WHERE LOWER(owner_id) = LOWER(%s) AND key = %s
                RETURNING key
                """,
                (owner_id, integration_key, owner_id, integration_key),
            )
            return bool(rows)
        connection = self.connections.pop((owner_id, integration_key), None)
        if connection and connection.get("connectionId"):
            self.connection_resources.pop(str(connection["connectionId"]), None)
        return (
            self.integrations.get(owner_id, {}).pop(integration_key, None) is not None
        )

    def save_tool(
        self, owner_id: str, integration_key: str, tool: ToolSpec
    ) -> ToolSpec | None:
        integration = self.get_integration(owner_id, integration_key)
        if not integration:
            return None
        integration.tools[tool.name] = tool
        self.save_integration(owner_id, integration)
        return tool

    def update_tool(
        self,
        owner_id: str,
        integration_key: str,
        tool_name: str,
        patch: ToolAccessPatch,
    ) -> ToolSpec | None:
        integration = self.get_integration(owner_id, integration_key)
        if not integration or tool_name not in integration.tools:
            return None
        current = integration.tools[tool_name]
        if current.maxAccessMode:
            priority = {"disabled": 0, "approval_required": 1, "enabled": 2}
            requested = patch.accessMode
            if priority.get(requested, 0) > priority.get(current.maxAccessMode, 0):
                raise ValueError(
                    f"Tool '{tool_name}' cannot exceed the organization-managed '{current.maxAccessMode}' access level."
                )
        next_tool = current.model_copy(
            update={
                "accessMode": patch.accessMode,
                "enabled": patch.accessMode != "disabled",
            }
        )
        integration.tools[tool_name] = next_tool
        self.save_integration(owner_id, integration)
        return next_tool

    def create_agent_key_value(self) -> str:
        return f"cla_{token_urlsafe(24)}"

    def row_to_permission_profile(self, row: dict[str, Any]) -> PermissionProfile:
        return PermissionProfile(
            id=str(row["id"]),
            ownerId=str(row["owner_id"]),
            name=str(row["name"]) if row.get("name") is not None else None,
            snapshot=PermissionProfileSnapshot.model_validate(
                row.get("snapshot") or {}
            ),
            agentApiKey=self.stored_agent_key_value(row),
            createdAt=self.iso(row.get("created_at")) or now_iso(),
            updatedAt=self.iso(row.get("updated_at")) or now_iso(),
        )

    def ensure_permission_profile_api_key(
        self, owner_id: str, profile_id: str
    ) -> dict[str, Any]:
        if self.database_url:
            value = self.create_agent_key_value()
            rows = self.db_rows(
                """
                INSERT INTO agent_api_keys (
                    profile_id, owner_id, api_key, api_key_hash, key_prefix,
                    key_suffix, encrypted_api_key, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (profile_id) DO NOTHING
                RETURNING *
                """,
                (
                    profile_id,
                    owner_id,
                    self.agent_key_storage_marker(value),
                    self.hash_key(value),
                    value[:12],
                    value[-4:],
                    encrypt_api_key(value),
                ),
            )
            if rows:
                return {**rows[0], "api_key": value}
            rows = self.db_rows(
                "SELECT * FROM agent_api_keys WHERE profile_id = %s",
                (profile_id,),
            )
            row = rows[0]
            return {**row, "api_key": self.stored_agent_key_value(row)}
        value = self.profile_agent_keys.get(profile_id)
        if value is None:
            value = self.create_agent_key_value()
            self.profile_agent_keys[profile_id] = value
        return {
            "profile_id": profile_id,
            "owner_id": owner_id,
            "api_key": value,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }

    def rotate_permission_profile_api_key(
        self, owner_id: str, profile_id: str, value: str | None = None
    ) -> dict[str, Any]:
        value = value or self.create_agent_key_value()
        if self.database_url:
            rows = self.db_rows(
                """
                INSERT INTO agent_api_keys (
                    profile_id, owner_id, api_key, api_key_hash, key_prefix,
                    key_suffix, encrypted_api_key, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (profile_id) DO UPDATE SET
                    owner_id = EXCLUDED.owner_id,
                    api_key = EXCLUDED.api_key,
                    api_key_hash = EXCLUDED.api_key_hash,
                    key_prefix = EXCLUDED.key_prefix,
                    key_suffix = EXCLUDED.key_suffix,
                    encrypted_api_key = EXCLUDED.encrypted_api_key,
                    updated_at = NOW()
                RETURNING *
                """,
                (
                    profile_id,
                    owner_id,
                    self.agent_key_storage_marker(value),
                    self.hash_key(value),
                    value[:12],
                    value[-4:],
                    encrypt_api_key(value),
                ),
            )
            return {**rows[0], "api_key": value}
        updated_at = now_iso()
        self.profile_agent_keys[profile_id] = value
        profile = self.permission_profiles.get(owner_id, {}).get(profile_id)
        if profile is not None:
            self.permission_profiles[owner_id][profile_id] = profile.model_copy(
                update={"agentApiKey": value, "updatedAt": updated_at}
            )
        return {
            "profile_id": profile_id,
            "owner_id": owner_id,
            "api_key": value,
            "created_at": updated_at,
            "updated_at": updated_at,
        }

    def list_permission_profiles(self, owner_id: str) -> list[PermissionProfile]:
        if self.database_url:
            rows = self.db_rows(
                """
                SELECT p.*, k.api_key, k.encrypted_api_key, k.key_prefix, k.key_suffix
                FROM permission_profiles p
                LEFT JOIN agent_api_keys k ON k.profile_id = p.id
                WHERE LOWER(p.owner_id) = LOWER(%s)
                ORDER BY p.name IS NOT NULL ASC, p.updated_at DESC, p.name ASC
                """,
                (owner_id,),
            )
            return [self.row_to_permission_profile(row) for row in rows]
        return list(self.permission_profiles.get(owner_id, {}).values())

    def get_permission_profile(
        self, owner_id: str, profile_id: str
    ) -> PermissionProfile | None:
        if self.database_url:
            rows = self.db_rows(
                """
                SELECT p.*, k.api_key, k.encrypted_api_key, k.key_prefix, k.key_suffix
                FROM permission_profiles p
                LEFT JOIN agent_api_keys k ON k.profile_id = p.id
                WHERE LOWER(p.owner_id) = LOWER(%s) AND p.id = %s
                """,
                (owner_id, profile_id),
            )
            return self.row_to_permission_profile(rows[0]) if rows else None
        return self.permission_profiles.get(owner_id, {}).get(profile_id)

    def get_default_permission_profile(self, owner_id: str) -> PermissionProfile | None:
        if self.database_url:
            rows = self.db_rows(
                """
                SELECT p.*, k.api_key, k.encrypted_api_key, k.key_prefix, k.key_suffix
                FROM permission_profiles p
                LEFT JOIN agent_api_keys k ON k.profile_id = p.id
                WHERE LOWER(p.owner_id) = LOWER(%s) AND p.name IS NULL
                LIMIT 1
                """,
                (owner_id,),
            )
            return self.row_to_permission_profile(rows[0]) if rows else None
        for profile in self.permission_profiles.get(owner_id, {}).values():
            if profile.name is None:
                return profile
        return None

    def get_or_create_default_permission_profile(
        self, owner_id: str
    ) -> PermissionProfile:
        profile = self.get_default_permission_profile(owner_id)
        if profile is not None:
            return profile
        return self.save_permission_profile(
            owner_id,
            PermissionProfile(
                ownerId=owner_id,
                name=None,
                snapshot=PermissionProfileSnapshot(),
            ),
        )

    def permission_profile_name_exists(
        self, owner_id: str, name: str, exclude_id: str | None = None
    ) -> bool:
        normalized_name = name.strip().casefold()
        for profile in self.list_permission_profiles(owner_id):
            if profile.name is None:
                continue
            if exclude_id and profile.id == exclude_id:
                continue
            if profile.name.strip().casefold() == normalized_name:
                return True
        return False

    def save_permission_profile(
        self, owner_id: str, profile: PermissionProfile
    ) -> PermissionProfile:
        if self.database_url:
            self.execute_db(
                """
                INSERT INTO permission_profiles (
                    id, owner_id, name, snapshot, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    owner_id = EXCLUDED.owner_id,
                    name = EXCLUDED.name,
                    snapshot = EXCLUDED.snapshot,
                    updated_at = NOW()
                """,
                (
                    profile.id,
                    owner_id,
                    profile.name,
                    Jsonb(profile.snapshot.model_dump()),
                    profile.createdAt,
                ),
            )
            self.ensure_permission_profile_api_key(owner_id, profile.id)
            saved = self.get_permission_profile(owner_id, profile.id)
            return saved or profile
        api_key = profile.agentApiKey or self.profile_agent_keys.get(profile.id)
        if not api_key:
            api_key = self.create_agent_key_value()
        self.profile_agent_keys[profile.id] = api_key
        profile = profile.model_copy(
            update={
                "ownerId": owner_id,
                "agentApiKey": api_key,
                "updatedAt": now_iso(),
            }
        )
        self.permission_profiles.setdefault(owner_id, {})[profile.id] = profile
        return profile

    def delete_permission_profile(self, owner_id: str, profile_id: str) -> bool:
        profile = self.get_permission_profile(owner_id, profile_id)
        if profile is None:
            return False
        if profile.name is None:
            return False

        if self.database_url:
            self.execute_db(
                "DELETE FROM agent_api_keys WHERE profile_id = %s",
                (profile_id,),
            )
            rows = self.db_rows(
                """
                DELETE FROM permission_profiles
                WHERE id = %s AND owner_id = %s
                RETURNING id
                """,
                (profile_id, owner_id),
            )
            return bool(rows)

        profiles = self.permission_profiles.get(owner_id, {})
        if profile_id not in profiles:
            return False
        del profiles[profile_id]
        self.profile_agent_keys.pop(profile_id, None)
        return True

    def row_to_access_request(self, row: dict[str, Any]) -> AccessRequest:
        return AccessRequest(
            id=str(row["id"]),
            agentId=row.get("agent_id"),
            agentClass=row.get("agent_class"),
            integrationKey=str(row["integration_key"]),
            toolName=str(row["function_name"]),
            scopes=row.get("scopes") or [],
            requestedMinutes=int(row.get("requested_minutes") or 240),
            channels=row.get("channels") or [],
            notificationTargets=row.get("notification_targets") or {},
            justification=str(row.get("justification") or ""),
            agentData=row.get("agent_data") or {},
            createdAt=self.iso(row.get("created_at")) or now_iso(),
            status=row.get("status") or "pending",
            decidedAt=self.iso(row.get("decided_at")),
            decidedBy=row.get("decided_by"),
        )

    def list_access_requests(self, owner_id: str | None = None) -> list[AccessRequest]:
        if self.database_url:
            if owner_id:
                rows = self.db_rows(
                    """
                    SELECT * FROM access_requests
                    WHERE LOWER(COALESCE(notification_targets->>'in_app', '')) = LOWER(%s)
                    ORDER BY created_at DESC
                    """,
                    (owner_id,),
                )
                return [self.row_to_access_request(row) for row in rows]
            rows = self.db_rows(
                "SELECT * FROM access_requests ORDER BY created_at DESC"
            )
            return [self.row_to_access_request(row) for row in rows]
        requests = [
            request
            for requests in self.access_requests.values()
            for request in requests.values()
        ]
        if owner_id:
            return [
                request
                for request in requests
                if self.access_request_matches_owner(request, owner_id)
            ]
        return requests

    def find_matching_pending_access_request(
        self,
        owner_id: str,
        agent_id: str | None,
        agent_class: str | None,
        integration_key: str,
        tool_name: str,
        scopes: list[str],
    ) -> AccessRequest | None:
        if self.database_url:
            rows = self.db_rows(
                """
                SELECT * FROM access_requests
                WHERE status = 'pending'
                  AND LOWER(COALESCE(notification_targets->>'in_app', '')) = LOWER(%s)
                  AND COALESCE(agent_id, '') = COALESCE(%s, '')
                  AND COALESCE(agent_class, '') = COALESCE(%s, '')
                  AND integration_key = %s
                  AND function_name = %s
                ORDER BY created_at DESC
                """,
                (owner_id, agent_id, agent_class, integration_key, tool_name),
            )
            for row in rows:
                request = self.row_to_access_request(row)
                if self.same_scopes(request.scopes, scopes):
                    return request
            return None
        for request in self.access_requests.get(
            self.normalize_owner_id(owner_id), {}
        ).values():
            if request.status != "pending":
                continue
            if (
                request.agentId == agent_id
                and request.agentClass == agent_class
                and request.integrationKey == integration_key
                and request.toolName == tool_name
                and self.same_scopes(request.scopes, scopes)
            ):
                return request
        return None

    def save_access_request(
        self, owner_id: str, request: AccessRequest
    ) -> AccessRequest:
        request = self.owner_scoped_access_request(owner_id, request)
        if self.database_url:
            data = dict(request.agentData)
            self.execute_db(
                """
                INSERT INTO access_requests (
                    id, agent_id, agent_class, integration_key, function_name, scopes,
                    requested_minutes, channels, notification_targets, justification, agent_data,
                    created_at, status, decided_at, decided_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    agent_id = EXCLUDED.agent_id,
                    agent_class = EXCLUDED.agent_class,
                    integration_key = EXCLUDED.integration_key,
                    function_name = EXCLUDED.function_name,
                    scopes = EXCLUDED.scopes,
                    requested_minutes = EXCLUDED.requested_minutes,
                    channels = EXCLUDED.channels,
                    notification_targets = EXCLUDED.notification_targets,
                    justification = EXCLUDED.justification,
                    agent_data = EXCLUDED.agent_data,
                    status = EXCLUDED.status,
                    decided_at = EXCLUDED.decided_at,
                    decided_by = EXCLUDED.decided_by
                """,
                (
                    request.id,
                    request.agentId,
                    request.agentClass,
                    request.integrationKey,
                    request.toolName,
                    Jsonb(request.scopes),
                    request.requestedMinutes,
                    Jsonb(request.channels),
                    Jsonb(request.notificationTargets),
                    request.justification,
                    Jsonb(data),
                    request.createdAt,
                    request.status,
                    request.decidedAt,
                    request.decidedBy,
                ),
            )
            request.agentData = data
            return request
        self.access_requests.setdefault(owner_id, {})[request.id] = request
        return request

    def decide_access_request(
        self, owner_id: str | None, request_id: str, status: str, reviewer: str
    ) -> AccessRequest | None:
        if self.database_url:
            decided_at = now_iso()
            rows = self.db_rows(
                """
                UPDATE access_requests
                SET status = %s, decided_at = %s, decided_by = %s
                WHERE id = %s
                  AND (
                    %s::text IS NULL
                    OR LOWER(COALESCE(notification_targets->>'in_app', '')) = LOWER(%s)
                  )
                RETURNING *
                """,
                (status, decided_at, reviewer, request_id, owner_id, owner_id),
            )
            return self.row_to_access_request(rows[0]) if rows else None
        for request in [
            request
            for requests in self.access_requests.values()
            for request in requests.values()
        ]:
            if request.id != request_id:
                continue
            if owner_id and not self.access_request_matches_owner(request, owner_id):
                continue
            request.status = status  # type: ignore[assignment]
            request.decidedAt = now_iso()
            request.decidedBy = reviewer
            return request
        return None

    def row_to_managed_connector(self, row: dict[str, Any]) -> ManagedConnector:
        return ManagedConnector(
            slug=str(row["slug"]),
            name=str(row["name"]),
            description=str(row["description"]),
            logoUrl=row.get("logo_url"),
            iconBg=row.get("icon_bg"),
            iconColor=row.get("icon_color"),
            appUrl=row.get("app_url"),
            docsUrl=row.get("docs_url"),
            categories=row.get("categories") or [],
            authModes=row.get("auth_modes") or [],
            authStrategy=row["auth_strategy"],
            provider=row.get("provider") or "mcp",
            oauthConfigured=bool(row.get("encrypted_oauth_client")),
            credentialLabel=str(row["credential_label"]),
            credentialPlaceholder=str(row["credential_placeholder"]),
            credentialHelp=str(row["credential_help"]),
            apiBaseUrl=row.get("api_base_url") or None,
            serverUrl=row.get("server_url"),
            openApiUrl=row.get("open_api_url"),
            oauthConfig=row.get("oauth_config"),
            connectionModel=row.get("connection_model"),
            tools=row.get("functions") or [],
            enabled=bool(row["enabled"]),
            createdAt=row["created_at"].isoformat()
            if hasattr(row.get("created_at"), "isoformat")
            else str(row.get("created_at")),
            updatedAt=row["updated_at"].isoformat()
            if hasattr(row.get("updated_at"), "isoformat")
            else str(row.get("updated_at")),
        )

    def list_managed_connectors(self) -> list[ManagedConnector]:
        if self.database_url:
            rows = self.db_rows("SELECT * FROM managed_connectors ORDER BY slug ASC")
            return [self.row_to_managed_connector(row) for row in rows]
        return list(self.managed_connectors.values())

    def save_managed_connector(self, connector: ManagedConnector) -> ManagedConnector:
        connector.updatedAt = now_iso()
        if connector.authStrategy == "oauth2":
            oauth_config = connector.oauthConfig
            # Dynamic OAuth connectors (no catalog authorizationUrl) delegate
            # the OAuth dance to the MCP server. They are configured as soon
            # as metadata is discoverable — no client_id is needed.
            if oauth_config and oauth_config.dynamicOAuth:
                connector.oauthConfigured = True
            else:
                connector.oauthConfigured = bool(connector.oauthClientId)
        else:
            connector.oauthConfigured = connector.authStrategy != "oauth2"
        if not connector.authModes:
            connector.authModes = [connector.authStrategy]
        if self.database_url:
            self.execute_db(
                """
                INSERT INTO managed_connectors (
                    slug, name, description, logo_url, icon_bg, icon_color, app_url, docs_url, categories,
                    auth_modes, auth_strategy, provider, credential_label, credential_placeholder,
                    credential_help, api_base_url, server_url, open_api_url, oauth_config,
                    connection_model, encrypted_oauth_client, functions, enabled, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (slug) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    logo_url = EXCLUDED.logo_url,
                    icon_bg = EXCLUDED.icon_bg,
                    icon_color = EXCLUDED.icon_color,
                    app_url = EXCLUDED.app_url,
                    docs_url = EXCLUDED.docs_url,
                    categories = EXCLUDED.categories,
                    auth_modes = EXCLUDED.auth_modes,
                    auth_strategy = EXCLUDED.auth_strategy,
                    provider = EXCLUDED.provider,
                    credential_label = EXCLUDED.credential_label,
                    credential_placeholder = EXCLUDED.credential_placeholder,
                    credential_help = EXCLUDED.credential_help,
                    api_base_url = EXCLUDED.api_base_url,
                    server_url = EXCLUDED.server_url,
                    open_api_url = EXCLUDED.open_api_url,
                    oauth_config = EXCLUDED.oauth_config,
                    connection_model = EXCLUDED.connection_model,
                    encrypted_oauth_client = COALESCE(EXCLUDED.encrypted_oauth_client, managed_connectors.encrypted_oauth_client),
                    functions = EXCLUDED.functions,
                    enabled = EXCLUDED.enabled,
                    updated_at = NOW()
                """,
                (
                    connector.slug,
                    connector.name,
                    connector.description,
                    connector.logoUrl,
                    connector.iconBg,
                    connector.iconColor,
                    connector.appUrl,
                    connector.docsUrl,
                    Jsonb(connector.categories),
                    Jsonb(connector.authModes),
                    connector.authStrategy,
                    connector.provider,
                    connector.credentialLabel,
                    connector.credentialPlaceholder,
                    connector.credentialHelp,
                    connector.apiBaseUrl or "",
                    connector.serverUrl,
                    connector.openApiUrl,
                    Jsonb(connector.oauthConfig.model_dump(exclude_none=True))
                    if connector.oauthConfig
                    else None,
                    Jsonb(connector.connectionModel.model_dump(exclude_none=True))
                    if connector.connectionModel
                    else None,
                    encrypt_credentials(
                        {
                            "clientId": connector.oauthClientId,
                            **(
                                {"clientSecret": connector.oauthClientSecret}
                                if connector.oauthClientSecret
                                else {}
                            ),
                        }
                    )
                    if connector.oauthClientId
                    else ("configured" if connector.oauthConfigured else None),
                    Jsonb([tool.model_dump() for tool in connector.tools]),
                    connector.enabled,
                ),
            )
            return connector
        self.managed_connectors[connector.slug] = connector
        return connector

    def delete_managed_connector(self, slug: str) -> bool:
        if self.database_url:
            rows = self.db_rows(
                "DELETE FROM managed_connectors WHERE slug = %s RETURNING slug", (slug,)
            )
            return bool(rows)
        return self.managed_connectors.pop(slug, None) is not None

    def delete_connections_by_integration_key(self, integration_key: str) -> bool:
        if self.database_url:
            rows = self.db_rows(
                "DELETE FROM integration_connections WHERE integration_key = %s OR provider = %s RETURNING integration_key",
                (integration_key, integration_key),
            )
            return bool(rows)
        deleted = False
        for owner, key in list(self.connections):
            connection = self.connections.get((owner, key))
            if (
                key != integration_key
                and (connection or {}).get("provider") != integration_key
            ):
                continue
            connection = self.connections.pop((owner, key), None)
            if connection and connection.get("connectionId"):
                self.connection_resources.pop(str(connection["connectionId"]), None)
            deleted = deleted or connection is not None
        return deleted

    def get_managed_connector(self, slug: str) -> ManagedConnector | None:
        if self.database_url:
            rows = self.db_rows(
                "SELECT * FROM managed_connectors WHERE slug = %s", (slug,)
            )
            return self.row_to_managed_connector(rows[0]) if rows else None
        return self.managed_connectors.get(slug)

    @staticmethod
    def connection_resource_id(
        connection_id: str, resource_type: str, external_resource_id: str
    ) -> str:
        raw = f"{connection_id}:{resource_type}:{external_resource_id}"
        return "res_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def list_connection_resources(
        self, connection_ids: list[str]
    ) -> list[dict[str, Any]]:
        if not connection_ids:
            return []
        if self.database_url:
            rows = self.db_rows(
                """
                SELECT * FROM connection_resources
                WHERE connection_id = ANY(%s)
                ORDER BY display_name ASC NULLS LAST, external_resource_id ASC
                """,
                (connection_ids,),
            )
        else:
            rows = [
                resource
                for connection_id in connection_ids
                for resource in self.connection_resources.get(connection_id, [])
            ]
        return [
            {
                "id": str(row.get("id")),
                "connectionId": str(
                    row.get("connection_id") or row.get("connectionId")
                ),
                "resourceType": str(
                    row.get("resource_type") or row.get("resourceType")
                ),
                "externalResourceId": str(
                    row.get("external_resource_id") or row.get("externalResourceId")
                ),
                "displayName": row.get("display_name") or row.get("displayName"),
                "externalUrl": row.get("external_url") or row.get("externalUrl"),
                "metadata": row.get("metadata") or {},
                "createdAt": self.iso(row.get("created_at") or row.get("createdAt"))
                or now_iso(),
                "updatedAt": self.iso(row.get("updated_at") or row.get("updatedAt"))
                or now_iso(),
            }
            for row in rows
        ]

    def replace_connection_resources(
        self,
        owner_id: str,
        connection_id: str,
        resources: Sequence[ConnectionResourceInput | dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized = []
        for raw_resource in resources:
            resource = ConnectionResourceInput.model_validate(raw_resource)
            resource_type = resource.resourceType.strip() or "resource"
            external_id = resource.externalResourceId.strip()
            if not external_id:
                continue
            normalized.append(
                {
                    "id": self.connection_resource_id(
                        connection_id, resource_type, external_id
                    ),
                    "connectionId": connection_id,
                    "resourceType": resource_type,
                    "externalResourceId": external_id,
                    "displayName": resource.displayName,
                    "externalUrl": resource.externalUrl,
                    "metadata": resource.metadata,
                    "createdAt": now_iso(),
                    "updatedAt": now_iso(),
                }
            )
        if self.database_url:
            resource_ids = [resource["id"] for resource in normalized]
            with psycopg.connect(self.database_url) as conn:
                with conn.cursor() as cur:
                    if resource_ids:
                        cur.execute(
                            "DELETE FROM connection_resources WHERE connection_id = %s AND NOT (id = ANY(%s))",
                            (connection_id, resource_ids),
                        )
                    else:
                        cur.execute(
                            "DELETE FROM connection_resources WHERE connection_id = %s",
                            (connection_id,),
                        )
                    for resource in normalized:
                        cur.execute(
                            """
                            INSERT INTO connection_resources (
                                id, connection_id, owner_id, resource_type,
                                external_resource_id, display_name, external_url,
                                metadata, created_at, updated_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                            ON CONFLICT (connection_id, resource_type, external_resource_id)
                            DO UPDATE SET display_name = EXCLUDED.display_name,
                                external_url = EXCLUDED.external_url,
                                metadata = EXCLUDED.metadata,
                                updated_at = NOW()
                            """,
                            (
                                resource["id"],
                                connection_id,
                                owner_id,
                                resource["resourceType"],
                                resource["externalResourceId"],
                                resource.get("displayName"),
                                resource.get("externalUrl"),
                                Jsonb(resource.get("metadata") or {}),
                            ),
                        )
                conn.commit()
            return self.list_connection_resources([connection_id])
        self.connection_resources[connection_id] = normalized
        return normalized

    def list_connections(
        self,
        owner_id: str | None = None,
        filters: AdminConnectionFilters | None = None,
    ) -> list[dict[str, Any]]:
        if self.database_url:
            owner_filter = owner_id or (filters.ownerId if filters else None)
            provider = filters.provider if filters else None
            integration_key = filters.integrationKey if filters else None
            limit = filters.limit if filters else 100
            offset = filters.offset if filters else 0
            rows = self.db_rows(
                """
                SELECT * FROM integration_connections
                WHERE (%s::text IS NULL OR LOWER(owner_id) = LOWER(%s))
                  AND (%s::text IS NULL OR LOWER(provider) = LOWER(%s))
                  AND (%s::text IS NULL OR LOWER(integration_key) = LOWER(%s))
                ORDER BY provider ASC, external_account_id ASC NULLS LAST, owner_id ASC, integration_key ASC
                LIMIT %s OFFSET %s
                """,
                (
                    owner_filter,
                    owner_filter,
                    provider,
                    provider,
                    integration_key,
                    integration_key,
                    limit,
                    offset,
                ),
            )
        else:
            rows = []
            for (saved_owner, _), record in self.connections.items():
                if owner_id and saved_owner.lower() != owner_id.lower():
                    continue
                if (
                    filters
                    and filters.ownerId
                    and saved_owner.lower() != filters.ownerId.lower()
                ):
                    continue
                if (
                    filters
                    and filters.provider
                    and str(record.get("provider", "")).lower()
                    != filters.provider.lower()
                ):
                    continue
                if (
                    filters
                    and filters.integrationKey
                    and record.get("integrationKey") != filters.integrationKey
                ):
                    continue
                rows.append(
                    {
                        "owner_id": saved_owner,
                        "connection_id": record.get("connectionId"),
                        "integration_key": record.get("integrationKey"),
                        "provider": record.get("provider"),
                        "auth_strategy": record.get("authStrategy"),
                        "token_type": record.get("tokenType"),
                        "scopes": record.get("scopes") or [],
                        "expires_at": record.get("expiresAt"),
                        "external_account_id": record.get("externalAccountId"),
                        "external_workspace_id": record.get("externalWorkspaceId"),
                        "display_name": record.get("displayName"),
                        "metadata": record.get("metadata") or {},
                        "created_at": record.get("createdAt"),
                        "updated_at": record.get("updatedAt"),
                    }
                )
        query = filters.q if filters and filters.q else ""
        resources_by_connection: dict[str, list[dict[str, Any]]] = {}
        connection_ids = [
            str(row.get("connection_id")) for row in rows if row.get("connection_id")
        ]
        for resource in self.list_connection_resources(connection_ids):
            resources_by_connection.setdefault(
                str(resource["connectionId"]), []
            ).append(resource)
        result = []
        for row in rows:
            connection_id = str(row.get("connection_id") or "")
            connection_metadata = row.get("metadata") or {}
            item = ConnectionSummary.model_validate(
                {
                    "ownerId": str(row.get("owner_id") or ""),
                    "connectionId": connection_id,
                    "integrationKey": str(row.get("integration_key") or ""),
                    "provider": str(row.get("provider") or ""),
                    "authStrategy": row.get("auth_strategy") or "oauth2",
                    "tokenType": row.get("token_type"),
                    "scopes": row.get("scopes") or [],
                    "expiresAt": self.iso(row.get("expires_at")),
                    "externalAccountId": row.get("external_account_id"),
                    "externalWorkspaceId": row.get("external_workspace_id"),
                    "externalTenantId": connection_metadata.get("externalTenantId"),
                    "displayName": row.get("display_name"),
                    "resources": resources_by_connection.get(connection_id, []),
                    "metadata": connection_metadata,
                    "createdAt": self.iso(row.get("created_at")) or now_iso(),
                    "updatedAt": self.iso(row.get("updated_at")) or now_iso(),
                }
            ).model_dump()
            if (
                query
                and query.lower()
                not in " ".join(str(v or "") for v in item.values()).lower()
            ):
                continue
            if owner_id:
                item.pop("ownerId", None)
            if filters and filters.admin:
                item.pop("metadata", None)
                for resource in item["resources"]:
                    resource.pop("metadata", None)
            result.append(item)
        return result

    def list_notifications(self) -> list[dict[str, Any]]:
        if self.database_url:
            rows = self.db_rows("SELECT * FROM notifications ORDER BY created_at DESC")
            return [
                NotificationRecord(
                    id=str(row["id"]),
                    channel=str(row["channel"]),
                    recipient=str(row["recipient"]),
                    subject=str(row["subject"]),
                    body=str(row["body"]),
                    metadata=row.get("metadata") or {},
                    createdAt=self.iso(row.get("created_at")) or now_iso(),
                ).model_dump()
                for row in rows
            ]
        return list(self.notifications)

    def create_notification(self, record: NotificationRecord) -> dict[str, Any]:
        payload = record.model_dump()
        if self.database_url:
            self.execute_db(
                """
                INSERT INTO notifications (
                    id, channel, recipient, subject, body, metadata, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.id,
                    record.channel,
                    record.recipient,
                    record.subject,
                    record.body,
                    Jsonb(record.metadata),
                    record.createdAt,
                ),
            )
            return payload
        self.notifications.append(payload)
        return payload

    def update_notification_metadata(
        self, notification_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        if self.database_url:
            rows = self.db_rows(
                "SELECT * FROM notifications WHERE id = %s",
                (notification_id,),
            )
            if not rows:
                return None
            metadata = dict(rows[0].get("metadata") or {})
            metadata.update(updates)
            self.execute_db(
                "UPDATE notifications SET metadata = %s WHERE id = %s",
                (Jsonb(metadata), notification_id),
            )
            record = NotificationRecord(
                id=str(rows[0]["id"]),
                channel=str(rows[0]["channel"]),
                recipient=str(rows[0]["recipient"]),
                subject=str(rows[0]["subject"]),
                body=str(rows[0]["body"]),
                metadata=metadata,
                createdAt=self.iso(rows[0].get("created_at")) or now_iso(),
            )
            return record.model_dump()
        for index, item in enumerate(self.notifications):
            if item.get("id") != notification_id:
                continue
            metadata = dict(item.get("metadata") or {})
            metadata.update(updates)
            updated = {**item, "metadata": metadata}
            self.notifications[index] = updated
            return updated
        return None

    def list_integration_requests(self) -> list[dict[str, Any]]:
        return [
            record.model_dump()
            for notification in self.list_notifications()
            if (record := integration_request_from_notification(notification))
        ]

    def decide_integration_request(
        self,
        request_id: str,
        status: str,
        reviewer: str,
    ) -> dict[str, Any] | None:
        notification = next(
            (
                item
                for item in self.list_notifications()
                if item.get("id") == request_id
            ),
            None,
        )
        if not notification:
            return None
        current = integration_request_from_notification(notification)
        if current is None:
            return None
        if current.status != "pending":
            raise ValueError("This request has already been decided.")
        updated = self.update_notification_metadata(
            request_id,
            {
                "status": status,
                "decidedBy": reviewer,
                "decidedAt": now_iso(),
            },
        )
        if not updated:
            return None
        decided = integration_request_from_notification(updated)
        return decided.model_dump() if decided else None

    def read_oauth_client(self, connector: ManagedConnector) -> dict[str, str] | None:
        if connector.oauthClientId:
            result = {"clientId": connector.oauthClientId}
            if connector.oauthClientSecret:
                result["clientSecret"] = connector.oauthClientSecret
            return result
        if not self.database_url:
            return None
        rows = self.db_rows(
            "SELECT encrypted_oauth_client FROM managed_connectors WHERE slug = %s",
            (connector.slug,),
        )
        encrypted = rows[0].get("encrypted_oauth_client") if rows else None
        if not encrypted or encrypted == "configured":
            return None
        data = decrypt_credentials(encrypted)
        client_id = str(data.get("clientId") or "").strip()
        if not client_id:
            return None
        result = {"clientId": client_id}
        client_secret = str(data.get("clientSecret") or "").strip()
        if client_secret:
            result["clientSecret"] = client_secret
        return result

    def save_oauth_state(
        self,
        state: str,
        owner_id: str,
        integration_key: str,
        provider: str,
        code_verifier: str | None,
        redirect_to: str,
        redirect_uri: str,
        callback_url: str | None,
        expires_at: str,
    ) -> None:
        oauth_state = OAuthState(
            state=state,
            ownerId=owner_id,
            integrationKey=integration_key,
            provider=provider,
            codeVerifier=code_verifier,
            redirectTo=redirect_to,
            redirectUri=redirect_uri,
            callbackUrl=callback_url,
            expiresAt=expires_at,
        )
        encrypted_verifier = (
            encrypt_credentials({"code_verifier": code_verifier})
            if code_verifier
            else None
        )
        if self.database_url:
            self.execute_db(
                """INSERT INTO oauth_states (state, owner_id, integration_key, provider, code_verifier, encrypted_code_verifier, redirect_to, redirect_uri, callback_url, created_at, expires_at)
                VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s, NOW(), %s)
                ON CONFLICT (state) DO UPDATE SET owner_id = EXCLUDED.owner_id, integration_key = EXCLUDED.integration_key, provider = EXCLUDED.provider, code_verifier = NULL, encrypted_code_verifier = EXCLUDED.encrypted_code_verifier, redirect_to = EXCLUDED.redirect_to, redirect_uri = EXCLUDED.redirect_uri, callback_url = EXCLUDED.callback_url, expires_at = EXCLUDED.expires_at""",
                (
                    state,
                    owner_id,
                    integration_key,
                    provider,
                    encrypted_verifier,
                    redirect_to,
                    redirect_uri,
                    callback_url,
                    expires_at,
                ),
            )
        else:
            self.oauth_states[state] = oauth_state

    def consume_oauth_state(self, state: str, provider: str) -> OAuthState | None:
        if self.database_url:
            rows = self.db_rows(
                "DELETE FROM oauth_states WHERE state = %s AND provider = %s AND expires_at > NOW() RETURNING *",
                (state, provider),
            )
            if not rows:
                return None
            row = rows[0]
            encrypted_verifier = row.get("encrypted_code_verifier")
            code_verifier = (
                decrypt_credentials(str(encrypted_verifier)).get("code_verifier")
                if encrypted_verifier
                else row.get("code_verifier")
            )
            return OAuthState(
                state=str(row["state"]),
                ownerId=str(row["owner_id"]),
                integrationKey=str(row["integration_key"]),
                provider=str(row["provider"]),
                codeVerifier=str(code_verifier) if code_verifier else None,
                redirectTo=str(row.get("redirect_to") or ""),
                redirectUri=str(row.get("redirect_uri") or ""),
                callbackUrl=str(row["callback_url"])
                if row.get("callback_url")
                else None,
                expiresAt=self.iso(row.get("expires_at")) or now_iso(),
            )
        row = self.oauth_states.pop(state, None)
        return row if row and row.provider == provider else None

    def record_tool_invocation(
        self,
        owner_id: str,
        integration: IntegrationSpec,
        tool: ToolSpec,
        scopes: list[str],
        payload: dict[str, Any],
        outcome: str,
        error_message: str | None = None,
        agent_id: str | None = None,
        agent_class: str | None = None,
    ) -> None:
        invoked_at = now_iso()
        if not self.database_url:
            self.tool_invocations.append(
                {
                    "id": token_urlsafe(18),
                    "ownerId": owner_id,
                    "agentId": agent_id,
                    "agentClass": agent_class,
                    "integrationKey": integration.key,
                    "connectionId": integration.config.get("connectionId"),
                    "resourceId": integration.config.get("resourceId"),
                    "toolName": tool.name,
                    "scopes": scopes,
                    "provider": integration.provider,
                    "kind": integration.kind,
                    "authStrategy": integration.authStrategy,
                    "outcome": outcome,
                    "invokedAt": invoked_at,
                    "payloadKeys": list(payload.keys()),
                    "errorMessage": error_message,
                }
            )
            return
        self.execute_db(
            """INSERT INTO tool_invocations (id, owner_id, agent_id, agent_class, integration_key, connection_id, resource_id, function_name, scopes, provider, kind, auth_strategy, outcome, invoked_at, duration_ms, payload_keys, error_message)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s)""",
            (
                token_urlsafe(18),
                owner_id,
                agent_id,
                agent_class,
                integration.key,
                integration.config.get("connectionId"),
                integration.config.get("resourceId"),
                tool.name,
                Jsonb(scopes),
                integration.provider,
                integration.kind,
                integration.authStrategy,
                outcome,
                None,
                Jsonb(list(payload.keys())),
                error_message,
            ),
        )

    def save_connection(
        self,
        owner_id: str,
        integration_key: str,
        provider: str,
        auth_strategy: str,
        credentials: OAuthCredentials | dict[str, Any],
        metadata: OAuthConnectionMetadata | dict[str, Any],
        resources: Sequence[ConnectionResourceInput | dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        oauth_credentials = OAuthCredentials.model_validate(credentials)
        oauth_metadata = OAuthConnectionMetadata.model_validate(metadata)
        encrypted = encrypt_credentials(oauth_credentials.model_dump(exclude_none=True))
        existing = self.get_connection(owner_id, integration_key)
        existing_connection = (
            OAuthConnection.model_validate(existing) if existing else None
        )
        metadata_connection_id = oauth_metadata.metadata.get("connectionId")
        connection_id = str(
            metadata_connection_id
            or (existing_connection.connectionId if existing_connection else None)
            or f"conn_{token_urlsafe(18)}"
        )
        if self.database_url:
            self.execute_db(
                """INSERT INTO integration_connections (connection_id, owner_id, integration_key, provider, auth_strategy, encrypted_credentials, token_type, scopes, expires_at, external_account_id, external_workspace_id, display_name, metadata, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (owner_id, integration_key) DO UPDATE SET provider = EXCLUDED.provider, auth_strategy = EXCLUDED.auth_strategy, encrypted_credentials = EXCLUDED.encrypted_credentials, token_type = EXCLUDED.token_type, scopes = EXCLUDED.scopes, expires_at = EXCLUDED.expires_at, external_account_id = EXCLUDED.external_account_id, external_workspace_id = EXCLUDED.external_workspace_id, display_name = EXCLUDED.display_name, metadata = EXCLUDED.metadata, updated_at = NOW()""",
                (
                    connection_id,
                    owner_id,
                    integration_key,
                    provider,
                    auth_strategy,
                    encrypted,
                    oauth_metadata.tokenType,
                    Jsonb(oauth_metadata.scopes),
                    oauth_metadata.expiresAt,
                    oauth_metadata.externalAccountId,
                    oauth_metadata.externalWorkspaceId,
                    oauth_metadata.displayName,
                    Jsonb(oauth_metadata.metadata),
                ),
            )
        connection = OAuthConnection(
            ownerId=owner_id,
            connectionId=connection_id,
            integrationKey=integration_key,
            provider=provider,
            authStrategy="oauth2",
            credentials=oauth_credentials,
            resources=[
                ConnectionResource.model_validate(resource)
                for resource in (
                    existing_connection.resources if existing_connection else []
                )
            ],
            **oauth_metadata.model_dump(),
        )
        if resources is not None:
            normalized_resources = self.replace_connection_resources(
                owner_id, connection_id, resources
            )
            connection = connection.model_copy(
                update={
                    "resources": [
                        ConnectionResource.model_validate(resource)
                        for resource in normalized_resources
                    ]
                }
            )
        self.connections[(owner_id, integration_key)] = connection.model_dump(
            exclude_none=True
        )
        return connection.model_dump(exclude={"ownerId", "credentials"})

    def get_connection(
        self, owner_id: str, integration_key: str
    ) -> dict[str, Any] | None:
        if self.database_url:
            rows = self.db_rows(
                "SELECT * FROM integration_connections WHERE LOWER(owner_id) = LOWER(%s) AND integration_key = %s",
                (owner_id, integration_key),
            )
            if not rows:
                return None
            row = rows[0]
            return OAuthConnection(
                ownerId=str(row.get("owner_id") or owner_id),
                connectionId=str(row["connection_id"]),
                integrationKey=str(row.get("integration_key") or integration_key),
                provider=str(row.get("provider") or ""),
                authStrategy="oauth2",
                credentials=OAuthCredentials.model_validate(
                    decrypt_credentials(row["encrypted_credentials"])
                ),
                tokenType=row.get("token_type"),
                scopes=row.get("scopes") or [],
                expiresAt=self.iso(row.get("expires_at")),
                externalAccountId=row.get("external_account_id"),
                externalWorkspaceId=row.get("external_workspace_id"),
                externalTenantId=(row.get("metadata") or {}).get("externalTenantId"),
                displayName=row.get("display_name"),
                metadata=row.get("metadata") or {},
                resources=[
                    ConnectionResource.model_validate(resource)
                    for resource in self.list_connection_resources(
                        [str(row.get("connection_id"))]
                    )
                ],
                createdAt=self.iso(row.get("created_at")) or now_iso(),
                updatedAt=self.iso(row.get("updated_at")) or now_iso(),
            ).model_dump(exclude_none=True)
        record = self.connections.get(
            (owner_id, integration_key)
        ) or self.connections.get((owner_id.lower(), integration_key))
        if not record:
            return None
        return OAuthConnection.model_validate(
            {"ownerId": owner_id, **record}
        ).model_dump(exclude_none=True)

    def get_connection_by_id(
        self, owner_id: str, connection_id: str
    ) -> OAuthConnection | None:
        if self.database_url:
            rows = self.db_rows(
                "SELECT integration_key FROM integration_connections WHERE LOWER(owner_id) = LOWER(%s) AND connection_id = %s",
                (owner_id, connection_id),
            )
            record = (
                self.get_connection(owner_id, str(rows[0]["integration_key"]))
                if rows
                else None
            )
            return OAuthConnection.model_validate(record) if record else None
        for (saved_owner, integration_key), record in self.connections.items():
            if (
                saved_owner.lower() == owner_id.lower()
                and record.get("connectionId") == connection_id
            ):
                record = self.get_connection(owner_id, integration_key)
                return OAuthConnection.model_validate(record) if record else None
        return None

    def get_connection_credentials(
        self,
        owner_id: str,
        integration_key: str,
        connection_id: str | None = None,
    ) -> dict[str, Any] | None:
        record = (
            self.get_connection_by_id(owner_id, connection_id)
            if connection_id
            else self.get_connection(owner_id, integration_key)
        )
        if not record:
            return None
        if isinstance(record, OAuthConnection):
            return record.credentials.model_dump(exclude_none=True)
        return OAuthConnection.model_validate(record).credentials.model_dump(
            exclude_none=True
        )

    def has_agent_api_keys(self) -> bool:
        if self.database_url:
            return bool(self.db_rows("SELECT owner_id FROM agent_api_keys LIMIT 1"))
        return bool(self.profile_agent_keys or self.agent_keys)

    def find_agent_api_key(self, api_key: str) -> dict[str, Any] | None:
        value = api_key.strip()
        if not value:
            return None
        if self.database_url:
            rows = self.db_rows(
                """
                SELECT k.*, p.id AS permission_profile_id, p.name AS permission_profile_name
                FROM agent_api_keys k
                JOIN permission_profiles p ON p.id = k.profile_id
                WHERE k.api_key_hash = %s
                """,
                (self.hash_key(value),),
            )
            if not rows:
                return None
            row = rows[0]
            return AgentApiKeyRecord(
                ownerId=str(row["owner_id"]),
                apiKey=value,
                permissionProfileId=row.get("permission_profile_id"),
                permissionProfileName=row.get("permission_profile_name"),
                createdAt=self.iso(row.get("created_at")) or now_iso(),
                updatedAt=self.iso(row.get("updated_at")) or now_iso(),
            ).model_dump()
        for owner_id, profiles in self.permission_profiles.items():
            for profile_id in profiles:
                if self.profile_agent_keys.get(profile_id) == value:
                    profile = profiles[profile_id]
                    return AgentApiKeyRecord(
                        ownerId=owner_id,
                        apiKey=value,
                        permissionProfileId=profile_id,
                        permissionProfileName=profile.name,
                        createdAt=profile.createdAt,
                        updatedAt=profile.updatedAt,
                    ).model_dump()
        for owner_id, stored in self.agent_keys.items():
            if stored == value:
                profile = self.get_default_permission_profile(owner_id)
                return AgentApiKeyRecord(
                    ownerId=owner_id,
                    apiKey=stored,
                    permissionProfileId=profile.id if profile else None,
                    createdAt=now_iso(),
                    updatedAt=now_iso(),
                ).model_dump()
        return None

    def find_scoped_api_key(self, kind: str, api_key: str) -> dict[str, Any] | None:
        value = api_key.strip()
        if not value:
            return None
        if not self.database_url:
            bucket = {"user": self.user_keys, "admin": self.admin_keys}[kind]
            for owner_id, stored in bucket.items():
                if stored == value:
                    return AgentApiKeyRecord(
                        ownerId=owner_id,
                        apiKey=stored,
                        createdAt=now_iso(),
                        updatedAt=now_iso(),
                    ).model_dump()
            return None
        table = "user_api_keys" if kind == "user" else "admin_api_keys"
        rows = self.db_rows(
            f"SELECT * FROM {table} WHERE api_key_hash = %s", (self.hash_key(value),)
        )
        if not rows:
            return None
        row = rows[0]
        return AgentApiKeyRecord(
            ownerId=str(row["owner_id"]),
            apiKey=value,
            createdAt=self.iso(row.get("created_at")) or now_iso(),
            updatedAt=self.iso(row.get("updated_at")) or now_iso(),
        ).model_dump()

    def get_key(self, owner_id: str, kind: str) -> dict[str, Any]:
        if kind == "agent":
            profile = self.get_or_create_default_permission_profile(owner_id)
            if profile.agentApiKey:
                key = {
                    "api_key": profile.agentApiKey,
                    "created_at": profile.createdAt,
                    "updated_at": profile.updatedAt,
                }
            else:
                key = self.rotate_permission_profile_api_key(owner_id, profile.id)
            value = str(key["api_key"])
            self.agent_keys[owner_id] = value
            return {
                "apiKey": value,
                "key": self.key_status(
                    value, key.get("created_at"), key.get("updated_at")
                ),
                "hasKey": True,
                "permissionProfileId": profile.id,
            }
        if not self.database_url:
            bucket = {"user": self.user_keys, "admin": self.admin_keys}[kind]
            value = bucket.get(owner_id)
            return {
                "apiKey": value,
                "key": self.key_status(value),
                "hasKey": value is not None,
            }
        table = "user_api_keys" if kind == "user" else "admin_api_keys"
        rows = self.db_rows(f"SELECT * FROM {table} WHERE owner_id = %s", (owner_id,))
        if not rows:
            return {"apiKey": None, "key": None, "hasKey": False}
        row = rows[0]
        value = self.decrypt_api_key_value(row.get("encrypted_api_key"))
        return {
            "apiKey": value,
            "key": self.stored_key_status(
                row.get("key_prefix"),
                row.get("key_suffix"),
                row.get("created_at"),
                row.get("updated_at"),
            ),
            "hasKey": True,
        }

    def save_key(self, owner_id: str, kind: str, value: str) -> dict[str, Any]:
        if kind == "agent":
            profile = self.get_or_create_default_permission_profile(owner_id)
            key = self.rotate_permission_profile_api_key(owner_id, profile.id, value)
            self.agent_keys[owner_id] = value
            return {
                "apiKey": value,
                "key": self.key_status(
                    value, key.get("created_at"), key.get("updated_at")
                ),
                "permissionProfileId": profile.id,
            }
        if not self.database_url:
            bucket = {"user": self.user_keys, "admin": self.admin_keys}[kind]
            bucket[owner_id] = value
            return {"apiKey": value, "key": self.key_status(value)}
        table = "user_api_keys" if kind == "user" else "admin_api_keys"
        self.execute_db(
            f"""INSERT INTO {table} (owner_id, api_key_hash, key_prefix, key_suffix, encrypted_api_key, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (owner_id) DO UPDATE SET api_key_hash = EXCLUDED.api_key_hash, key_prefix = EXCLUDED.key_prefix,
            key_suffix = EXCLUDED.key_suffix, encrypted_api_key = EXCLUDED.encrypted_api_key, updated_at = NOW()""",
            (
                owner_id,
                self.hash_key(value),
                value[:12],
                value[-4:],
                encrypt_api_key(value),
            ),
        )
        return {"apiKey": value, "key": self.key_status(value)}

    def delete_key(self, owner_id: str, kind: str) -> bool:
        if kind == "agent":
            profile = self.get_default_permission_profile(owner_id)
            if profile is None:
                return False
            self.agent_keys.pop(owner_id, None)
            self.profile_agent_keys.pop(profile.id, None)
            profiles = self.permission_profiles.get(owner_id, {})
            if profile.id in profiles:
                profiles[profile.id] = profile.model_copy(update={"agentApiKey": ""})
            if self.database_url:
                rows = self.db_rows(
                    "DELETE FROM agent_api_keys WHERE profile_id = %s RETURNING owner_id",
                    (profile.id,),
                )
                return bool(rows)
            return True
        if not self.database_url:
            bucket = {"user": self.user_keys, "admin": self.admin_keys}[kind]
            return bucket.pop(owner_id, None) is not None
        table = "user_api_keys" if kind == "user" else "admin_api_keys"
        rows = self.db_rows(
            f"DELETE FROM {table} WHERE owner_id = %s RETURNING owner_id", (owner_id,)
        )
        return bool(rows)

    def list_temporary_grants(
        self, owner_id: str | None = None
    ) -> list[dict[str, Any]]:

        if self.database_url:
            if owner_id:
                rows = self.db_rows(
                    """SELECT * FROM temporary_grants
                    WHERE LOWER(owner_id) = LOWER(%s)
                    ORDER BY created_at DESC""",
                    (owner_id,),
                )
            else:
                rows = self.db_rows(
                    "SELECT * FROM temporary_grants ORDER BY created_at DESC"
                )
            return [
                TemporaryGrant(
                    id=str(row["id"]),
                    ownerId=str(row.get("owner_id") or ""),
                    agentId=row.get("agent_id"),
                    agentClass=row.get("agent_class"),
                    integrationKey=str(row["integration_key"]),
                    toolName=str(row["function_name"]),
                    scopes=row.get("scopes") or [],
                    grantedBy=str(row["granted_by"]),
                    createdAt=self.iso(row.get("created_at")) or now_iso(),
                    expiresAt=self.iso(row.get("expires_at")) or now_iso(),
                ).model_dump()
                for row in rows
            ]
        grants = list(self.temporary_grants.values())
        if owner_id:
            return [
                grant
                for grant in grants
                if str(grant.get("ownerId") or "").lower() == owner_id.lower()
            ]
        return grants

    def save_temporary_grant(self, grant: dict[str, Any]) -> dict[str, Any]:
        validated_grant = TemporaryGrant.model_validate(grant)
        grant = validated_grant.model_dump()
        if self.database_url:
            self.execute_db(
                """INSERT INTO temporary_grants (id, owner_id, agent_id, agent_class, integration_key, function_name, scopes, granted_by, created_at, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET owner_id = EXCLUDED.owner_id, agent_id = EXCLUDED.agent_id, agent_class = EXCLUDED.agent_class, integration_key = EXCLUDED.integration_key, function_name = EXCLUDED.function_name, scopes = EXCLUDED.scopes, granted_by = EXCLUDED.granted_by, created_at = EXCLUDED.created_at, expires_at = EXCLUDED.expires_at""",
                (
                    grant["id"],
                    grant["ownerId"],
                    grant.get("agentId"),
                    grant.get("agentClass"),
                    grant["integrationKey"],
                    grant["toolName"],
                    Jsonb(grant.get("scopes") or []),
                    grant["grantedBy"],
                    grant["createdAt"],
                    grant["expiresAt"],
                ),
            )
        else:
            self.temporary_grants[grant["id"]] = grant
        return grant

    def list_owner_tool_usage_summaries(self, owner_id: str) -> list[dict[str, Any]]:
        if self.database_url:
            rows = self.db_rows(
                """SELECT integration_key, function_name, MAX(invoked_at) AS last_invoked_at,
                (ARRAY_AGG(outcome ORDER BY invoked_at DESC))[1] AS last_outcome, COUNT(*) AS invocation_count
                FROM tool_invocations WHERE LOWER(owner_id) = LOWER(%s)
                GROUP BY integration_key, function_name""",
                (owner_id,),
            )
            return [
                ToolUsageSummary(
                    integrationKey=str(row["integration_key"]),
                    toolName=str(row["function_name"]),
                    lastInvokedAt=self.iso(row.get("last_invoked_at")) or now_iso(),
                    lastInvocationOutcome=row["last_outcome"],
                    invocationCount=int(row.get("invocation_count") or 0),
                ).model_dump()
                for row in rows
            ]
        summaries: dict[tuple[str, str], dict[str, Any]] = {}
        for record in self.tool_invocations:
            if str(record.get("ownerId", "")).lower() != owner_id.lower():
                continue
            key = (str(record.get("integrationKey")), str(record.get("toolName")))
            current = summaries.get(key)
            if not current or str(record.get("invokedAt")) > str(
                current.get("lastInvokedAt")
            ):
                summaries[key] = {
                    "integrationKey": key[0],
                    "toolName": key[1],
                    "lastInvokedAt": str(record.get("invokedAt")),
                    "lastInvocationOutcome": record.get("outcome"),
                    "invocationCount": 0,
                }
            summaries[key]["invocationCount"] = (
                int(summaries[key].get("invocationCount") or 0) + 1
            )
        return [
            ToolUsageSummary.model_validate(summary).model_dump()
            for summary in summaries.values()
        ]

    def purge_expired_grants(self) -> int:
        if self.database_url:
            rows = self.db_rows(
                "DELETE FROM temporary_grants WHERE expires_at <= NOW() RETURNING id"
            )
            return len(rows)
        return 0

    def overview(self) -> dict[str, Any]:
        connections = self.list_connections(filters=AdminConnectionFilters(limit=1000))
        access_requests = self.list_access_requests(None)
        notifications = self.list_notifications()
        users: dict[str, dict[str, Any]] = {}
        for connection in connections:
            owner = connection.get("ownerId") or "unknown"
            summary = users.setdefault(
                owner,
                {
                    "ownerId": owner,
                    "totalConnections": 0,
                    "integrations": set(),
                    "providers": set(),
                    "totalAccessRequests": 0,
                    "pendingAccessRequests": 0,
                    "notifications": 0,
                    "duplicateExternalAccounts": [],
                    "connections": [],
                },
            )
            summary["totalConnections"] += 1
            summary["integrations"].add(connection.get("integrationKey"))
            summary["providers"].add(connection.get("provider"))
            summary["connections"].append(connection)
        for request in access_requests:
            owner = self.access_request_owner_id(request)
            if not owner or owner == "case-by-case-approvals":
                continue
            summary = users.setdefault(
                owner,
                {
                    "ownerId": owner,
                    "totalConnections": 0,
                    "integrations": set(),
                    "providers": set(),
                    "totalAccessRequests": 0,
                    "pendingAccessRequests": 0,
                    "notifications": 0,
                    "duplicateExternalAccounts": [],
                    "connections": [],
                },
            )
            summary["totalAccessRequests"] += 1
            if request.status == "pending":
                summary["pendingAccessRequests"] += 1
        duplicate_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for connection in connections:
            external_id = str(connection.get("externalAccountId") or "").strip().lower()
            provider = str(connection.get("provider") or "").strip().lower()
            if not external_id or not provider:
                continue
            duplicate_groups.setdefault((provider, external_id), []).append(connection)
        duplicates = [
            {
                "provider": provider,
                "externalAccountId": external_id,
                "connections": group,
                "owners": sorted(
                    {
                        str(item.get("ownerId") or "")
                        for item in group
                        if item.get("ownerId")
                    }
                ),
            }
            for (provider, external_id), group in duplicate_groups.items()
            if len(
                {(item.get("ownerId"), item.get("integrationKey")) for item in group}
            )
            > 1
        ]
        user_list = []
        for summary in users.values():
            summary["integrations"] = sorted(x for x in summary["integrations"] if x)
            summary["providers"] = sorted(x for x in summary["providers"] if x)
            summary["duplicateExternalAccounts"] = [
                dup for dup in duplicates if summary["ownerId"] in dup["owners"]
            ]
            user_list.append(summary)
        duplicate_models = [
            DuplicateExternalAccount.model_validate(duplicate)
            for duplicate in duplicates
        ]
        return AdminOverview(
            totalConnections=len(connections),
            connectedOwners=len(users),
            managedConnectors=len(self.list_managed_connectors()),
            totalAccessRequests=len(access_requests),
            pendingAccessRequests=sum(
                1 for r in access_requests if r.status == "pending"
            ),
            notifications=len(notifications),
            duplicateExternalAccounts=duplicate_models,
            users=user_list,
        ).model_dump()

    def rotate_key(self, bucket: dict[str, str], owner_id: str, prefix: str) -> str:
        value = f"{prefix}_{token_urlsafe(24)}"
        bucket[owner_id] = value
        return value


repository = Repository()
