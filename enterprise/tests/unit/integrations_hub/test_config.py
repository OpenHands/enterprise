from __future__ import annotations

import pytest

from integrations_hub.config import get_default_config, reset_config


def test_config_reads_only_prefixed_environment(monkeypatch) -> None:
    monkeypatch.setenv("INTHUB_POSTGRES_URL", "postgres://prefixed-db")
    monkeypatch.setenv("INTHUB_CRON_SECRET", "prefixed-cron-secret")
    monkeypatch.setenv(
        "INTHUB_APP_ADMIN_EMAILS",
        "Admin@Example.com, second-admin@example.com ",
    )
    monkeypatch.setenv("INTHUB_CREDENTIAL_ENCRYPTION_KEY", "prefixed-key")
    monkeypatch.setenv("INTHUB_POSTHOG_CLIENT_KEY", "phc_test")
    monkeypatch.setenv("INTHUB_OPENHANDS_AUTH_COOKIE_NAME", "custom_session")
    monkeypatch.setenv(
        "INTHUB_PUBLIC_BASE_URL", "https://hub.example.com/integrations-hub/"
    )
    monkeypatch.setenv("INTHUB_ROOT_PATH", "/integrations-hub/")
    monkeypatch.setenv("INTHUB_API_ROOT_PATH", "/api/integrations-hub/")
    monkeypatch.setenv("INTHUB_MAX_CONCURRENT_BLOCKING_REQUESTS", "3")
    monkeypatch.setenv("INTHUB_READINESS_TIMEOUT_SECONDS", "7.5")
    reset_config()

    config = get_default_config()

    assert config.postgres_url == "postgres://prefixed-db"
    assert config.cron_secret
    assert config.cron_secret.get_secret_value() == "prefixed-cron-secret"
    assert config.admin_email_list == ["admin@example.com", "second-admin@example.com"]
    assert config.is_admin_email("ADMIN@example.com")
    assert config.require_credential_encryption_key() == "prefixed-key"
    assert config.require_cron_secret() == "prefixed-cron-secret"
    assert config.posthog_client_key == "phc_test"
    assert config.openhands_auth_cookie_name == "custom_session"
    assert config.public_base_url == "https://hub.example.com/integrations-hub"
    assert config.root_path == "/integrations-hub"
    assert config.api_root_path == "/api/integrations-hub"
    assert config.max_concurrent_blocking_requests == 3
    assert config.readiness_timeout_seconds == 7.5


@pytest.mark.parametrize(
    "public_base_url",
    [
        "ftp://hub.example.com/integrations-hub",
        "https://user@hub.example.com/integrations-hub",
        "https://hub.example.com/integrations-hub?source=bad",
    ],
)
def test_public_base_url_rejects_unsafe_shapes(
    config_env, public_base_url: str
) -> None:
    config_env("INTHUB_ROOT_PATH", "/integrations-hub")
    config_env("INTHUB_PUBLIC_BASE_URL", public_base_url)

    with pytest.raises(ValueError, match="public base URL"):
        get_default_config()


def test_public_base_url_path_must_match_root_path(config_env) -> None:
    config_env("INTHUB_ROOT_PATH", "/integrations-hub")
    config_env("INTHUB_PUBLIC_BASE_URL", "https://hub.example.com/wrong-path")

    with pytest.raises(ValueError, match="INTHUB_ROOT_PATH"):
        get_default_config()


def test_config_ignores_unprefixed_legacy_env_names(monkeypatch) -> None:
    """Unprefixed legacy env names must never populate backend config."""
    monkeypatch.delenv("INTHUB_POSTGRES_URL", raising=False)
    monkeypatch.delenv("INTHUB_CRON_SECRET", raising=False)
    monkeypatch.delenv("INTHUB_APP_ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("INTHUB_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("INTHUB_POSTHOG_CLIENT_KEY", raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgres://legacy-db")
    monkeypatch.setenv("DATABASE_URL", "postgres://legacy-database-url")
    monkeypatch.setenv("CRON_SECRET", "legacy-cron-secret")
    monkeypatch.setenv("APP_ADMIN_EMAILS", "admin@example.com")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "legacy-key")
    monkeypatch.setenv("OPENHANDS_BASE_URL", "https://legacy.example.com")
    monkeypatch.setenv("POSTHOG_CLIENT_KEY", "phc_legacy")
    reset_config()

    config = get_default_config()

    assert config.postgres_url is None
    assert config.openhands_base_url is None
    assert config.admin_email_list == []
    assert not config.is_admin_email("admin@example.com")
    assert config.cron_secret is None
    assert config.credential_encryption_key is None
    assert config.posthog_client_key is None
    with pytest.raises(RuntimeError, match="INTHUB_CREDENTIAL_ENCRYPTION_KEY"):
        config.require_credential_encryption_key()
    with pytest.raises(RuntimeError, match="INTHUB_CRON_SECRET"):
        config.require_cron_secret()


def test_config_raises_when_sensitive_secrets_are_not_configured(monkeypatch) -> None:
    monkeypatch.delenv("INTHUB_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("INTHUB_CRON_SECRET", raising=False)
    reset_config()

    config = get_default_config()

    with pytest.raises(RuntimeError, match="INTHUB_CREDENTIAL_ENCRYPTION_KEY"):
        config.require_credential_encryption_key()
    with pytest.raises(RuntimeError, match="INTHUB_CRON_SECRET"):
        config.require_cron_secret()


def test_prefixed_admin_emails_accept_comma_separated_values(config_env) -> None:
    config_env("INTHUB_APP_ADMIN_EMAILS", "Admin@Example.com, ops@example.com")

    config = get_default_config()

    assert config.admin_email_list == ["admin@example.com", "ops@example.com"]


def test_prefixed_admin_emails_accept_json_arrays(config_env) -> None:
    config_env("INTHUB_APP_ADMIN_EMAILS", '["Admin@Example.com", "ops@example.com"]')

    config = get_default_config()

    assert config.admin_email_list == ["admin@example.com", "ops@example.com"]


def test_blank_prefixed_secret_is_unconfigured(config_env) -> None:
    """A blank/whitespace INTHUB_* secret resolves to None, not an empty secret.

    Operators who copy `.env.example` (which ships blank `INTHUB_*=` lines) to
    `.env` must not silently configure empty secrets.
    """
    config_env("INTHUB_CRON_SECRET", "  ")
    config_env("INTHUB_CREDENTIAL_ENCRYPTION_KEY", "")

    config = get_default_config()

    assert config.cron_secret is None
    assert config.credential_encryption_key is None
    with pytest.raises(RuntimeError, match="INTHUB_CREDENTIAL_ENCRYPTION_KEY"):
        config.require_credential_encryption_key()
    with pytest.raises(RuntimeError, match="INTHUB_CRON_SECRET"):
        config.require_cron_secret()


def test_nonempty_prefixed_secret_is_used_verbatim(config_env) -> None:
    """A real prefixed secret value is used as-is (not blank-stripped away)."""
    config_env("INTHUB_CREDENTIAL_ENCRYPTION_KEY", "real-key")

    config = get_default_config()

    assert config.require_credential_encryption_key() == "real-key"
