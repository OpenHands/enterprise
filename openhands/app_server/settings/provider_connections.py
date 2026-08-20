"""Container for shared LLM provider connections.

A *provider connection* is a small, named bundle of the credential material an
LLM profile would otherwise carry inline: an ``api_key`` and an optional
``base_url``. Several LLM profiles can reference one connection by id, so
rotating the shared key in one place updates every profile that points at it.

This mirrors :class:`~openhands.app_server.settings.llm_profiles.LLMProfiles`:
a container model persisted as a single ``EncryptedJSON`` blob on the ``org``
row. The column is the at-rest encryption boundary, so the ``api_key`` rides in
cleartext *inside* the encrypted envelope (the same contract ``llm_profiles``
uses for per-profile keys).

The envelope shape is ``{connections: {<id>: ProviderConnection}}``.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    SerializationInfo,
    ValidationError,
    field_serializer,
    field_validator,
)

from openhands.app_server.settings.llm_profiles import has_real_api_key
from openhands.app_server.utils.logger import openhands_logger as logger

# Connection ids: 1-128 chars, alphanumeric start, then alphanumeric/._-.
# Same shape as the SDK's CONNECTION_ID_PATTERN / profile names — blocks path
# separators and leading dots so an id is always a safe dict key.
CONNECTION_ID_PATTERN: str = r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$'
_CONNECTION_ID_REGEX: re.Pattern[str] = re.compile(CONNECTION_ID_PATTERN)


def _get_max_connections_per_org() -> int:
    """Max provider connections per org. Tunable via env, defaults to 64.

    Matches the SDK agent-server ``MAX_PROVIDER_CONNECTIONS`` so local and cloud
    enforce the same ceiling.
    """
    env_value = os.getenv('MAX_PROVIDER_CONNECTIONS_PER_ORG')
    if env_value is not None:
        try:
            value = int(env_value)
            if value <= 0:
                logger.warning(
                    'MAX_PROVIDER_CONNECTIONS_PER_ORG must be positive, using '
                    'default 64'
                )
                return 64
            return value
        except ValueError:
            logger.warning(
                'MAX_PROVIDER_CONNECTIONS_PER_ORG must be an integer, using default 64'
            )
            return 64
    return 64


MAX_CONNECTIONS_PER_ORG: int = _get_max_connections_per_org()


def now_epoch() -> int:
    return int(time.time())


class ProviderConnectionNotFoundError(LookupError):
    """Raised when a connection lookup references an unknown id."""

    def __init__(self, connection_id: str) -> None:
        self.connection_id = connection_id
        super().__init__(f"Provider connection '{connection_id}' not found")


class ProviderConnectionLimitExceededError(ValueError):
    """Raised when creating a connection would exceed the configured limit."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(
            f'Provider connection limit reached ({limit}). Delete one before '
            'creating another.'
        )


class ProviderConnectionInUseError(ValueError):
    """Raised when deleting a connection still referenced by a profile.

    Carries the referencing profile names so the router can surface them in the
    409 detail (mirrors the LLM-profile ``ProfileReferenced`` guard).
    """

    def __init__(self, connection_id: str, referrers: list[str]) -> None:
        self.connection_id = connection_id
        self.referrers = referrers
        joined = ', '.join(sorted(referrers))
        super().__init__(
            f"Provider connection '{connection_id}' is still referenced by "
            f'profile(s): {joined}'
        )


class ProviderConnection(BaseModel):
    """A shared credential bundle reused by one or more LLM profiles."""

    id: str = Field(..., min_length=1, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=128)
    provider: str = Field(default='custom', min_length=1, max_length=128)
    api_key: SecretStr | None = None
    base_url: str | None = Field(default=None, max_length=2048)
    created_at: int = Field(default_factory=now_epoch)
    updated_at: int = Field(default_factory=now_epoch)

    model_config = ConfigDict(validate_assignment=True)

    def api_key_value(self) -> str | None:
        """Return the plaintext key, or ``None`` when unset/empty."""
        if self.api_key is None:
            return None
        value = self.api_key.get_secret_value()
        return value if value.strip() else None

    @field_serializer('api_key', when_used='always')
    def _serialize_api_key(
        self, v: SecretStr | None, info: SerializationInfo
    ) -> str | None:
        """Expose the raw key only when the caller asks (persistence path).

        The ``org.provider_connections`` column encrypts the whole blob, so the
        stored envelope carries the cleartext key under ``expose_secrets`` —
        exactly like ``LLMProfiles``. API responses never dump with that flag;
        they use :class:`ProviderConnectionResponse` instead.
        """
        if v is None:
            return None
        if info.context and info.context.get('expose_secrets'):
            return v.get_secret_value()
        return None


class ProviderConnections(BaseModel):
    """Named collection of provider connections stored on an org row.

    Invariants (enforced on validate + assignment):
    - Each connection is keyed by its own ``id``.
    - Individual connections that fail to parse (schema drift) are dropped with
      a warning rather than failing the whole settings load — same contract as
      :class:`LLMProfiles`.
    """

    model_config = ConfigDict(validate_assignment=True)

    connections: dict[str, ProviderConnection] = Field(default_factory=dict)

    @field_validator('connections', mode='before')
    @classmethod
    def _skip_invalid_connections(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        valid: dict[str, Any] = {}
        for cid, raw in value.items():
            if isinstance(raw, ProviderConnection):
                valid[cid] = raw
                continue
            try:
                valid[cid] = ProviderConnection.model_validate(raw)
            except ValidationError as exc:
                logger.warning('Skipping invalid provider connection %r: %s', cid, exc)
        return valid

    # ── Queries ────────────────────────────────────────────────────

    def get(self, connection_id: str) -> ProviderConnection | None:
        return self.connections.get(connection_id)

    def require(self, connection_id: str) -> ProviderConnection:
        conn = self.connections.get(connection_id)
        if conn is None:
            raise ProviderConnectionNotFoundError(connection_id)
        return conn

    def has(self, connection_id: str) -> bool:
        return connection_id in self.connections

    def list(self) -> list[ProviderConnection]:
        return list(self.connections.values())

    def summaries(self) -> list[dict[str, Any]]:
        """Return a secret-free ``{id, display_name, provider, base_url,
        created_at, updated_at, api_key_set}`` dict per connection."""
        return [
            {
                'id': conn.id,
                'display_name': conn.display_name,
                'provider': conn.provider,
                'base_url': conn.base_url,
                'created_at': conn.created_at,
                'updated_at': conn.updated_at,
                'api_key_set': has_real_api_key(conn.api_key),
            }
            for conn in self.connections.values()
        ]

    # ── Mutations ──────────────────────────────────────────────────

    def create(self, conn: ProviderConnection) -> ProviderConnection:
        """Add a new connection. Raises on id collision, invalid id, or limit."""
        if not _CONNECTION_ID_REGEX.match(conn.id):
            raise ValueError(f'Invalid provider connection id: {conn.id!r}')
        if conn.id in self.connections:
            raise ValueError(f"Provider connection '{conn.id}' already exists")
        if len(self.connections) >= MAX_CONNECTIONS_PER_ORG:
            raise ProviderConnectionLimitExceededError(MAX_CONNECTIONS_PER_ORG)
        # Reassign the whole dict so validate_assignment re-runs the validator.
        self.connections = {**self.connections, conn.id: conn}
        return conn

    def update(self, conn: ProviderConnection) -> ProviderConnection:
        """Replace an existing connection. Raises if the id is unknown."""
        if conn.id not in self.connections:
            raise ProviderConnectionNotFoundError(conn.id)
        updated = {**self.connections, conn.id: conn}
        self.connections = updated
        return conn

    def delete(self, connection_id: str) -> bool:
        """Delete a connection. Returns True if it existed.

        Referential integrity (blocking delete while a profile still links to
        this connection) is enforced by the caller, which has both collections
        under the org-row lock — see the router's delete handler.
        """
        if connection_id not in self.connections:
            return False
        remaining = {
            cid: c for cid, c in self.connections.items() if cid != connection_id
        }
        self.connections = remaining
        return True

    # ── Serialization ──────────────────────────────────────────────

    @field_serializer('connections')
    def _connections_serializer(
        self,
        connections: dict[str, ProviderConnection],
        info: SerializationInfo,
    ) -> dict[str, Any]:
        return {
            cid: conn.model_dump(mode='json', context=info.context)
            for cid, conn in connections.items()
        }
