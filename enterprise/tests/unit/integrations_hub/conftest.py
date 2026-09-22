"""Pytest configuration for backend tests."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Protocol

import pytest

from integrations_hub.config import reset_config


class ConfigEnvFixture(Protocol):
    """Type for the config_env fixture callable."""

    def __call__(self, key: str, value: str | None = ...) -> None:
        """Set or delete an environment variable and reset config.

        Args:
            key: Environment variable name
            value: Value to set, or None to delete the variable
        """
        ...


@pytest.fixture(autouse=True)
def reset_config_after_test(monkeypatch):
    """Reset config cache and Hub path/DB env after each test."""
    # Avoid leak from mount.configure_integrations_hub_env setdefault.
    monkeypatch.delenv('INTHUB_API_ROOT_PATH', raising=False)
    reset_config()
    yield
    reset_config()


@pytest.fixture(autouse=True)
def local_auth_bypass(monkeypatch):
    """Test-only dashboard auth bypass.

    Production no longer has a runtime auth-bypass switch. Tests that need
    local dashboard access can set PYTEST_INTHUB_AUTH_BYPASS=true; the app code
    never reads this variable.
    """
    from integrations_hub import main as main_module

    real_openhands_user_from_request = main_module.openhands_user_from_request

    def fake_openhands_user_from_request(request):
        if os.getenv("PYTEST_INTHUB_AUTH_BYPASS") == "true":
            owner = (
                (request.headers.get("x-owner-id") or "dev@localhost").strip().lower()
            )
            is_admin = main_module.is_admin_owner(owner)
            return SimpleNamespace(
                id=owner,
                email=owner,
                owner_id=owner,
                normalized_org_id=None,
                org_name=None,
                role="owner" if is_admin else "member",
                permissions=(),
                is_admin=is_admin,
            )
        return real_openhands_user_from_request(request)

    monkeypatch.setattr(
        main_module, "openhands_user_from_request", fake_openhands_user_from_request
    )

    reset_config()


@pytest.fixture
def config_env(monkeypatch) -> ConfigEnvFixture:
    """Fixture for setting/deleting environment variables that affect config.

    Usage:
        def test_something(config_env):
            config_env("PYTEST_INTHUB_AUTH_BYPASS", "true")  # Set a value
            config_env("INTHUB_APP_ADMIN_EMAILS")      # Delete (value=None)
            # Config is automatically reset after each change
            ...
    """

    def setter(key: str, value: str | None = None) -> None:
        if value is None:
            monkeypatch.delenv(key, raising=False)
            if key == "PYTEST_INTHUB_AUTH_BYPASS":
                monkeypatch.delenv("INTHUB_ALLOW_PRIVATE_CONNECTOR_URLS", raising=False)
        else:
            monkeypatch.setenv(key, value)
            if key == "PYTEST_INTHUB_AUTH_BYPASS" and value == "true":
                monkeypatch.setenv("INTHUB_ALLOW_PRIVATE_CONNECTOR_URLS", "true")
        reset_config()  # Immediately reset so new config picks up change

    return setter
