from __future__ import annotations

import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi import HTTPException

from integrations_hub import main as main_module
from integrations_hub.main import discover_mcp_tools, invoke_http_tool
from integrations_hub.models import IntegrationSpec, ToolSpec
from integrations_hub.openapi_tools import generate_openapi_tools
from integrations_hub.url_security import urlopen_no_redirect, validate_external_url


class ConfigEnvFixtureProtocol:
    def __call__(self, key: str, value: str | None) -> None: ...


def test_validate_external_url_rejects_localhost_when_private_urls_disallowed(
    config_env: ConfigEnvFixtureProtocol,
) -> None:
    config_env("INTHUB_ALLOW_PRIVATE_CONNECTOR_URLS", "false")

    with pytest.raises(HTTPException) as exc:
        validate_external_url(
            "https://127.0.0.1:8443/openapi.json", purpose="OpenAPI schema"
        )

    assert exc.value.status_code == 400
    assert "private" in str(exc.value.detail).lower()


def test_validate_external_url_allows_localhost_when_private_urls_allowed(
    config_env: ConfigEnvFixtureProtocol,
) -> None:
    config_env("INTHUB_ALLOW_PRIVATE_CONNECTOR_URLS", "true")

    assert (
        validate_external_url(
            "http://127.0.0.1:8080/openapi.json", purpose="OpenAPI schema"
        )
        == "http://127.0.0.1:8080/openapi.json"
    )


def test_validate_external_url_rejects_embedded_credentials(
    config_env: ConfigEnvFixtureProtocol,
) -> None:
    config_env("INTHUB_ALLOW_PRIVATE_CONNECTOR_URLS", "false")

    with pytest.raises(HTTPException) as exc:
        validate_external_url(
            "https://user:pass@example.com/api", purpose="HTTP API base"
        )

    assert exc.value.status_code == 400
    assert "credentials" in str(exc.value.detail).lower()


def test_generate_openapi_tools_rejects_private_schema_url(
    config_env: ConfigEnvFixtureProtocol,
) -> None:
    config_env("INTHUB_ALLOW_PRIVATE_CONNECTOR_URLS", "false")

    with pytest.raises(HTTPException) as exc:
        generate_openapi_tools("https://10.0.0.5/openapi.json", "oauth2")

    assert exc.value.status_code == 400
    assert "private" in str(exc.value.detail).lower()


def test_invoke_http_tool_rejects_private_api_base_url(
    config_env: ConfigEnvFixtureProtocol,
) -> None:
    config_env("INTHUB_ALLOW_PRIVATE_CONNECTOR_URLS", "false")
    integration = IntegrationSpec(
        key="internal",
        name="Internal",
        kind="api",
        provider="http",
        authStrategy="none",
        config={"apiBaseUrl": "https://192.168.0.10"},
        tools={},
    )
    tool = ToolSpec(
        name="get_metadata",
        config={"request": {"method": "GET", "path": "/metadata"}},
    )

    with pytest.raises(HTTPException) as exc:
        invoke_http_tool("owner@example.com", integration, tool, {})

    assert exc.value.status_code == 400
    assert "private" in str(exc.value.detail).lower()


@pytest.mark.parametrize(
    "url",
    [
        "https://[::1]/mcp",
        "https://[::ffff:127.0.0.1]/mcp",
        "https://169.254.169.254/latest/meta-data",
        "https://100.64.0.1/internal",
        "https://[64:ff9b::a9fe:a9fe]/metadata",
        "https://[64:ff9b::0a00:0005]/internal",
    ],
)
def test_validate_external_url_rejects_ipv6_and_metadata_targets(
    config_env: ConfigEnvFixtureProtocol,
    url: str,
) -> None:
    config_env("INTHUB_ALLOW_PRIVATE_CONNECTOR_URLS", "false")

    with pytest.raises(HTTPException) as exc:
        validate_external_url(url, purpose="MCP server")

    assert exc.value.status_code == 400


def test_validate_external_url_rejects_hostname_resolving_to_private_ip(
    config_env: ConfigEnvFixtureProtocol, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_env("INTHUB_ALLOW_PRIVATE_CONNECTOR_URLS", "false")

    def fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(HTTPException) as exc:
        validate_external_url("https://public.example.com/api", purpose="HTTP API base")

    assert exc.value.status_code == 400
    assert "private" in str(exc.value.detail).lower()


def test_validate_external_url_rejects_hostname_resolving_to_shared_address_space(
    config_env: ConfigEnvFixtureProtocol, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_env("INTHUB_ALLOW_PRIVATE_CONNECTOR_URLS", "false")

    def fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("100.64.0.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(HTTPException) as exc:
        validate_external_url("https://shared.example.com/api", purpose="HTTP API base")

    assert exc.value.status_code == 400
    assert "private" in str(exc.value.detail).lower()


def test_validate_external_url_rejects_hostname_resolving_to_nat64_metadata(
    config_env: ConfigEnvFixtureProtocol, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_env("INTHUB_ALLOW_PRIVATE_CONNECTOR_URLS", "false")

    def fake_getaddrinfo(*_args, **_kwargs):
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("64:ff9b::a9fe:a9fe", 443))
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(HTTPException) as exc:
        validate_external_url("https://nat64.example.com/api", purpose="HTTP API base")

    assert exc.value.status_code == 400
    assert "private" in str(exc.value.detail).lower()


class CapturingClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_mcp_discovery_does_not_follow_redirects(
    config_env: ConfigEnvFixtureProtocol, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_env("INTHUB_ALLOW_PRIVATE_CONNECTOR_URLS", "false")
    clients: list[CapturingClient] = []

    def fake_client(**kwargs):
        client = CapturingClient(**kwargs)
        clients.append(client)
        return client

    responses = iter(
        [
            ({"protocolVersion": "2025-06-18"}, "session"),
            (None, "session"),
            ({"tools": []}, "session"),
        ]
    )

    monkeypatch.setattr(main_module.httpx, "Client", fake_client)
    monkeypatch.setattr(
        main_module, "post_mcp_message", lambda *_args, **_kwargs: next(responses)
    )

    assert discover_mcp_tools("https://mcp.example.com/mcp", {}) == {}
    assert clients
    assert clients[0].kwargs["follow_redirects"] is False


def test_urlopen_no_redirect_does_not_follow_location_header() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/private")
            self.end_headers()

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/redirect"
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urlopen_no_redirect(request, timeout=5)
        assert exc.value.code == 302
    finally:
        server.shutdown()
