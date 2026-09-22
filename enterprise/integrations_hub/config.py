"""Centralized configuration for the Integrations Hub backend.

This module provides a single source of truth for all environment-based
configuration. Access via `get_default_config()`.

Pattern follows the agent server config from the OpenHands SDK:
https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-agent-server/openhands/agent_server/config.py
"""

from __future__ import annotations

import json
from typing import ClassVar
from urllib.parse import urlsplit, urlunsplit

from openhands.agent_server.env_parser import from_env
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


# Environment variable prefix for all config fields
ENVIRONMENT_VARIABLE_PREFIX = "INTHUB"


def _parse_email_list(value: str) -> list[str]:
    """Parse admin emails from a comma-separated string or JSON array."""
    raw = value.strip()
    if not raw:
        return []
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            return [
                str(email).strip().lower() for email in parsed if str(email).strip()
            ]
    return [email.strip().lower() for email in raw.split(",") if email.strip()]


def normalize_public_base_url(value: object) -> str:
    """Normalize an externally visible HTTP(S) site URL."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("public base URL has an invalid port") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "public base URL must be an absolute HTTP(S) URL without "
            "credentials, query parameters, or a fragment"
        )
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


class Config(BaseModel):
    """Immutable configuration for the Integrations Hub backend.

    All settings are loaded from environment variables using the SDK's
    from_env() parser with prefix INTHUB.

    Environment variables:
        INTHUB_POSTGRES_URL: Database connection URL
        INTHUB_OPENHANDS_BASE_URL: OpenHands base URL for session validation
        INTHUB_OPENHANDS_AUTH_COOKIE_NAME: OpenHands browser session cookie name
        INTHUB_POSTHOG_CLIENT_KEY: Public PostHog project key for product analytics
        INTHUB_CRON_SECRET: Secret for cron job authentication
        INTHUB_APP_ADMIN_EMAILS: Comma-separated admin email addresses
        INTHUB_CREDENTIAL_ENCRYPTION_KEY: Key for encrypting stored credentials
        INTHUB_STATIC_DIR: Directory for pre-built SPA files (K8s mode only)
        INTHUB_PUBLIC_BASE_URL: Externally visible Integrations Hub site URL
        INTHUB_ROOT_PATH: Public UI prefix for the K8s SPA
        INTHUB_API_ROOT_PATH: Public API prefix for the K8s service
        INTHUB_MAX_CONCURRENT_BLOCKING_REQUESTS: Worker limit for sync backend work
        INTHUB_READINESS_TIMEOUT_SECONDS: Readiness database-check deadline
    """

    # Database
    postgres_url: str | None = Field(
        default=None,
        description=(
            "Postgres connection URL. Reads from INTHUB_POSTGRES_URL when set; "
            "otherwise enterprise mount fills this from shared OHE DB_* env vars."
        ),
    )

    # Authentication
    openhands_base_url: str | None = Field(
        default=None,
        description=(
            "OpenHands base URL used to validate session cookies and API keys. "
            "Reads from INTHUB_OPENHANDS_BASE_URL."
        ),
    )
    openhands_auth_cookie_name: str = Field(
        default="keycloak_auth",
        description=(
            "OpenHands browser session cookie forwarded for same-origin auth. "
            "Reads from INTHUB_OPENHANDS_AUTH_COOKIE_NAME."
        ),
    )
    posthog_client_key: str | None = Field(
        default=None,
        description=(
            "Public PostHog project key used to enable explicit browser analytics. "
            "Reads from INTHUB_POSTHOG_CLIENT_KEY."
        ),
    )
    cron_secret: SecretStr | None = Field(
        default=None,
        description=(
            "Secret for authenticating cron job requests. Reads from "
            "INTHUB_CRON_SECRET."
        ),
    )

    # Admin
    app_admin_emails: str = Field(
        default="",
        description=(
            "Comma-separated admin email addresses. Reads from INTHUB_APP_ADMIN_EMAILS."
        ),
    )

    # Encryption
    credential_encryption_key: SecretStr | None = Field(
        default=None,
        description=(
            "Key for encrypting stored provider credentials. Reads from "
            "INTHUB_CREDENTIAL_ENCRYPTION_KEY. Required for OAuth and credential "
            "storage."
        ),
    )

    # Connector URL safety
    allow_private_connector_urls: bool = Field(
        default=False,
        description=(
            "Allow connector-controlled outbound URLs to target localhost/private networks. "
            "Reads from INTHUB_ALLOW_PRIVATE_CONNECTOR_URLS. Keep false in production."
        ),
    )

    # Static file serving (K8s mode)
    static_dir: str | None = Field(
        default=None,
        description=(
            "Directory containing pre-built Next.js SPA static files. When set, "
            "FastAPI serves the SPA directly (for K8s/Docker deployments)."
        ),
    )
    public_base_url: str = Field(
        default="",
        description=(
            "Externally visible Integrations Hub site URL, including root_path. "
            "Reads from INTHUB_PUBLIC_BASE_URL."
        ),
    )
    root_path: str = Field(
        default="",
        description=(
            "Public path prefix used to serve the SPA. Reads from INTHUB_ROOT_PATH."
        ),
    )
    api_root_path: str = Field(
        default="",
        description=(
            "Public path prefix translated to the backend's internal /api routes. "
            "Reads from INTHUB_API_ROOT_PATH."
        ),
    )

    # Blocking I/O compatibility boundary
    max_concurrent_blocking_requests: int = Field(
        default=10,
        ge=1,
        description=(
            "Maximum synchronous backend requests allowed to occupy worker threads. "
            "Reads from INTHUB_MAX_CONCURRENT_BLOCKING_REQUESTS."
        ),
    )
    readiness_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        description=(
            "Maximum time to wait for the synchronous database readiness check. "
            "Reads from INTHUB_READINESS_TIMEOUT_SECONDS."
        ),
    )

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    # SecretStr fields where a blank/whitespace-only INTHUB_* env value must be
    # treated as "not configured" rather than an empty secret. The SDK env
    # parser reads any present env var verbatim, so without this an operator
    # who copies `.env.example` (which ships blank `INTHUB_*=` lines) to `.env`
    # would silently configure an empty secret instead of leaving it unset.
    _BLANK_IS_UNCONFIGURED_FIELDS: ClassVar[tuple[str, ...]] = (
        "cron_secret",
        "credential_encryption_key",
    )

    @field_validator("root_path", "api_root_path", mode="before")
    @classmethod
    def _normalize_path_prefix(cls, value: object) -> str:
        raw = str(value or "").strip()
        if not raw or raw == "/":
            return ""
        if not raw.startswith("/"):
            raise ValueError("path prefixes must start with /")
        return raw.rstrip("/")

    @field_validator("public_base_url", mode="before")
    @classmethod
    def _normalize_public_base_url(cls, value: object) -> str:
        return normalize_public_base_url(value)

    @field_validator("openhands_auth_cookie_name", mode="before")
    @classmethod
    def _validate_auth_cookie_name(cls, value: object) -> str:
        name = str(value or "").strip()
        if not name or any(character in name for character in ";=\r\n"):
            raise ValueError("auth cookie name must be a non-empty cookie token")
        return name

    @model_validator(mode="before")
    @classmethod
    def _blank_secret_is_unconfigured(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        for field_name in cls._BLANK_IS_UNCONFIGURED_FIELDS:
            if field_name not in data:
                continue
            value = data[field_name]
            if value is None:
                continue
            if isinstance(value, str):
                stripped = value.strip()
            elif isinstance(value, SecretStr):
                stripped = value.get_secret_value().strip()
            else:
                continue
            if not stripped:
                # Drop the blank value so the field resolves to its default
                # (None) instead of an empty SecretStr.
                del data[field_name]
        return data

    @model_validator(mode="after")
    def _public_base_path_matches_root_path(self) -> Config:
        if not self.public_base_url:
            return self
        public_path = urlsplit(self.public_base_url).path.rstrip("/")
        if public_path != self.root_path:
            expected = self.root_path or "/"
            actual = public_path or "/"
            raise ValueError(
                "INTHUB_PUBLIC_BASE_URL path must match INTHUB_ROOT_PATH "
                f"(expected {expected!r}, got {actual!r})"
            )
        return self

    @property
    def admin_email_list(self) -> list[str]:
        """Return normalized admin emails from the comma-separated allowlist."""
        return _parse_email_list(self.app_admin_emails)

    @property
    def openhands_user_me_url(self) -> str:
        if not self.openhands_base_url:
            return ""
        return f"{self.openhands_base_url.rstrip('/')}/api/v1/users/me"

    def is_admin_email(self, email: str | None) -> bool:
        """Check if an email address belongs to an admin."""
        if not email:
            return False
        return email.strip().lower() in self.admin_email_list

    def require_credential_encryption_key(self) -> str:
        """Get the credential encryption key or raise an error if not configured."""
        if not self.credential_encryption_key:
            raise RuntimeError(
                "INTHUB_CREDENTIAL_ENCRYPTION_KEY must be configured to encrypt "
                "stored provider credentials."
            )
        return self.credential_encryption_key.get_secret_value()

    def require_cron_secret(self) -> str:
        """Get the cron secret or raise an error if not configured."""
        if not self.cron_secret:
            raise RuntimeError(
                "INTHUB_CRON_SECRET must be configured for the expire-grants cron route."
            )
        return self.cron_secret.get_secret_value()


# Singleton config instance
_default_config: Config | None = None


def get_default_config() -> Config:
    """Get the default config shared across the application.

    The config is created once from environment variables using the SDK's
    from_env() parser with prefix INTHUB, then cached. When
    ``INTHUB_POSTGRES_URL`` is unset, ``postgres_url`` falls back to the shared
    OHE ``DB_*`` database.
    """
    global _default_config
    if _default_config is None:
        cfg = from_env(Config, ENVIRONMENT_VARIABLE_PREFIX)
        if not cfg.postgres_url:
            from integrations_hub.mount import resolve_shared_postgres_url

            shared = resolve_shared_postgres_url()
            if shared:
                cfg = cfg.model_copy(update={'postgres_url': shared})
        _default_config = cfg
    return _default_config


def reset_config() -> None:
    """Clear the cached config. Useful for testing."""
    global _default_config
    _default_config = None
