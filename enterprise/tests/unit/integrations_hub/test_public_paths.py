from __future__ import annotations

import pytest
from fastapi import HTTPException

from integrations_hub.oauth_flow import (
    build_oauth_callback_redirect,
    configured_oauth_callback_url,
    oauth_callback_base_url,
)
from integrations_hub.public_paths import (
    internal_api_path,
    public_api_path,
    public_openapi_paths,
    public_ui_path,
)


def test_public_paths_remain_root_relative_by_default() -> None:
    assert public_api_path("/api/integrations") == "/api/integrations"
    assert internal_api_path("/api/integrations") is None
    assert public_ui_path("/integrations") == "/integrations"


def test_public_paths_use_configured_prefixes(config_env) -> None:
    config_env("INTHUB_ROOT_PATH", "/integrations-hub/")
    config_env("INTHUB_API_ROOT_PATH", "/api/integrations-hub/")

    assert public_api_path("/api/integrations") == (
        "/api/integrations-hub/integrations"
    )
    assert internal_api_path("/api/integrations-hub/integrations") == (
        "/api/integrations"
    )
    assert public_ui_path("/integrations") == "/integrations-hub/integrations"
    assert public_ui_path("/integrations-hub/profile") == ("/integrations-hub/profile")
    assert public_openapi_paths({"/api/user/key": {}}) == {
        "/api/integrations-hub/user/key": {}
    }


def test_oauth_urls_use_configured_public_prefixes(config_env, monkeypatch) -> None:
    config_env("INTHUB_ROOT_PATH", "/integrations-hub")
    config_env("INTHUB_API_ROOT_PATH", "/api/integrations-hub")
    config_env(
        "INTHUB_PUBLIC_BASE_URL",
        "https://staging.all-hands.dev/integrations-hub",
    )
    monkeypatch.setenv(
        "INTHUB_OAUTH_REDIRECT_PROXY_URL", "https://staging.all-hands.dev"
    )

    assert configured_oauth_callback_url("linear") == (
        "https://staging.all-hands.dev/api/integrations-hub/oauth/linear/callback"
    )
    assert build_oauth_callback_redirect(
        "/integrations?showIntegrationWizard=1",
        oauth_status="connected",
        oauth_provider="linear",
    ).startswith("/integrations-hub/integrations?")


def test_oauth_callback_uses_configured_public_site(config_env) -> None:
    config_env("INTHUB_ROOT_PATH", "/integrations-hub")
    config_env("INTHUB_API_ROOT_PATH", "/api/integrations-hub")
    config_env(
        "INTHUB_PUBLIC_BASE_URL",
        "https://dev.all-hands.dev/integrations-hub",
    )

    assert oauth_callback_base_url() == (
        "https://dev.all-hands.dev/api/integrations-hub/oauth"
    )
    assert configured_oauth_callback_url("linear") == (
        "https://dev.all-hands.dev/api/integrations-hub/oauth/linear/callback"
    )


def test_oauth_callback_proxy_overrides_public_site(config_env, monkeypatch) -> None:
    config_env("INTHUB_ROOT_PATH", "/integrations-hub")
    config_env("INTHUB_API_ROOT_PATH", "/api/integrations-hub")
    config_env(
        "INTHUB_PUBLIC_BASE_URL",
        "https://pr-394.staging.all-hands.dev/integrations-hub",
    )
    monkeypatch.setenv(
        "INTHUB_OAUTH_REDIRECT_PROXY_URL", "https://staging.all-hands.dev"
    )

    assert configured_oauth_callback_url("linear") == (
        "https://staging.all-hands.dev/api/integrations-hub/oauth/linear/callback"
    )


def test_auth_url_is_the_only_deprecated_callback_fallback(
    config_env, monkeypatch
) -> None:
    config_env("INTHUB_PUBLIC_BASE_URL")
    monkeypatch.setenv("AUTH_URL", "https://legacy.example.dev/")

    with pytest.warns(
        DeprecationWarning,
        match="AUTH_URL.*INTHUB_PUBLIC_BASE_URL",
    ):
        assert configured_oauth_callback_url("linear") == (
            "https://legacy.example.dev/api/oauth/linear/callback"
        )


def test_oauth_callback_requires_explicit_configuration(
    config_env, monkeypatch
) -> None:
    config_env("INTHUB_PUBLIC_BASE_URL")
    monkeypatch.delenv("AUTH_URL", raising=False)
    monkeypatch.delenv("INTHUB_OAUTH_REDIRECT_PROXY_URL", raising=False)

    with pytest.raises(HTTPException, match="INTHUB_PUBLIC_BASE_URL") as exc_info:
        configured_oauth_callback_url("linear")

    assert exc_info.value.status_code == 500
