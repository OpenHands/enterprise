"""Tests for OHE-2815 security headers middleware."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.app_server.middleware import SecurityHeadersMiddleware


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get('/sample')
    def sample() -> dict[str, str]:
        return {'ok': 'yes'}

    @app.get('/assets/sample.js')
    def asset() -> dict[str, str]:
        return {'ok': 'asset'}

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def clear_csp_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for var in (
        'CONTENT_SECURITY_POLICY_REPORT_ONLY',
        'CONTENT_SECURITY_POLICY',
        'OH_RUNTIME_HOSTS',
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def _csp(response) -> str:
    return response.headers['Content-Security-Policy-Report-Only']


class TestSecurityHeadersMiddleware:
    def test_sets_report_only_header(
        self, client: TestClient, clear_csp_env: None
    ) -> None:
        response = client.get('/sample')

        assert response.status_code == 200
        csp = _csp(response)
        assert "default-src 'self'" in csp
        assert "script-src 'self' 'unsafe-inline'" in csp
        assert 'report-uri' not in csp

    def test_sets_companion_headers(
        self, client: TestClient, clear_csp_env: None
    ) -> None:
        response = client.get('/sample')

        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert response.headers['Referrer-Policy'] == 'strict-origin-when-cross-origin'
        assert 'camera=()' in response.headers['Permissions-Policy']
        assert 'interest-cohort=()' in response.headers['Permissions-Policy']
        assert response.headers['X-Frame-Options'] == 'SAMEORIGIN'

    def test_no_enforced_csp_header(
        self, client: TestClient, clear_csp_env: None
    ) -> None:
        response = client.get('/sample')

        assert 'Content-Security-Policy' not in response.headers
        assert 'Content-Security-Policy-Report-Only' in response.headers

    def test_header_applied_to_static_paths(
        self, client: TestClient, clear_csp_env: None
    ) -> None:
        response = client.get('/assets/sample.js')

        assert response.status_code == 200
        assert 'Content-Security-Policy-Report-Only' in response.headers

    def test_runtime_hosts_appended_to_frame_and_connect(
        self,
        client: TestClient,
        clear_csp_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            'OH_RUNTIME_HOSTS', 'https://*.staging-runtime.all-hands.dev'
        )

        response = client.get('/sample')

        csp = _csp(response)
        assert "frame-src 'self' https://*.staging-runtime.all-hands.dev;" in csp
        assert (
            "connect-src 'self' ws: wss: https://us.i.posthog.com"
            ' https://us-assets.i.posthog.com'
            ' https://*.staging-runtime.all-hands.dev;' in csp
        )

    def test_runtime_hosts_supports_multiple_wildcards(
        self,
        client: TestClient,
        clear_csp_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            'OH_RUNTIME_HOSTS',
            (
                'https://*.staging-runtime.all-hands.dev,'
                ' https://*.runtime.all-hands.dev,'
                ' https://vscode-staging.example.com'
            ),
        )

        response = client.get('/sample')

        csp = _csp(response)
        assert 'https://*.staging-runtime.all-hands.dev' in csp
        assert 'https://*.runtime.all-hands.dev' in csp
        assert 'https://vscode-staging.example.com' in csp
        # Both directives carry the whole host set.
        assert csp.count('https://*.runtime.all-hands.dev') == 2

    def test_posthog_assets_in_default_script_src(
        self, client: TestClient, clear_csp_env: None
    ) -> None:
        response = client.get('/sample')

        csp = _csp(response)
        assert "script-src 'self' 'unsafe-inline'" in csp
        assert 'https://us-assets.i.posthog.com' in csp
        assert 'https://us.i.posthog.com' in csp

    def test_empty_runtime_hosts_omits_extras(
        self, client: TestClient, clear_csp_env: None
    ) -> None:
        response = client.get('/sample')

        csp = _csp(response)
        # No trailing whitespace when no extras are configured.
        assert "frame-src 'self';" in csp
        assert "frame-src 'self' ;" not in csp

    def test_policy_override_takes_precedence(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom = "default-src 'none'"
        monkeypatch.setenv('CONTENT_SECURITY_POLICY_REPORT_ONLY', custom)

        response = client.get('/sample')

        assert _csp(response) == custom
