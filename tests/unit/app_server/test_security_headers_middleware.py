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
        'WEB_HOST',
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

    def test_web_host_wildcard_appended_to_frame_and_connect(
        self,
        client: TestClient,
        clear_csp_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('WEB_HOST', 'app.all-hands.dev')

        response = client.get('/sample')

        csp = _csp(response)
        assert "frame-src 'self' https://*.all-hands.dev;" in csp
        assert (
            "connect-src 'self' ws: wss: https://us.i.posthog.com"
            ' https://us-assets.i.posthog.com'
            ' https://*.all-hands.dev;' in csp
        )

    def test_web_host_wildcard_reaches_runtime_subdomain_tree(
        self,
        client: TestClient,
        clear_csp_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Staging: WEB_HOST=staging.all-hands.dev but the runtime lives at
        ``<sandbox>.staging-runtime.all-hands.dev`` — a sibling subdomain
        tree under the registrable domain. The wildcard must reach it.
        """
        monkeypatch.setenv('WEB_HOST', 'staging.all-hands.dev')

        response = client.get('/sample')

        csp = _csp(response)
        assert 'https://*.all-hands.dev' in csp
        # ``*.staging.all-hands.dev`` would NOT have matched the runtime.
        assert 'https://*.staging.all-hands.dev' not in csp

    def test_web_host_self_hosted_apex(
        self,
        client: TestClient,
        clear_csp_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('WEB_HOST', 'openhands.example.com')

        response = client.get('/sample')

        csp = _csp(response)
        assert 'https://*.example.com' in csp
        assert 'https://*.openhands.example.com' not in csp

    def test_web_host_with_scheme_prefix_is_stripped(
        self,
        client: TestClient,
        clear_csp_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('WEB_HOST', 'https://app.all-hands.dev')

        response = client.get('/sample')

        csp = _csp(response)
        assert 'https://*.all-hands.dev' in csp
        assert 'https://https://' not in csp

    def test_web_host_with_trailing_slash_is_stripped(
        self,
        client: TestClient,
        clear_csp_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('WEB_HOST', 'app.all-hands.dev/')

        response = client.get('/sample')

        csp = _csp(response)
        assert 'https://*.all-hands.dev' in csp
        assert 'https://*.all-hands.dev/' not in csp

    def test_web_host_with_port_strips_port(
        self,
        client: TestClient,
        clear_csp_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv('WEB_HOST', 'app.example.com:8443')

        response = client.get('/sample')

        csp = _csp(response)
        assert 'https://*.example.com' in csp
        assert ':8443' not in csp

    def test_posthog_assets_in_default_script_src(
        self, client: TestClient, clear_csp_env: None
    ) -> None:
        response = client.get('/sample')

        csp = _csp(response)
        assert "script-src 'self' 'unsafe-inline'" in csp
        assert 'https://us-assets.i.posthog.com' in csp
        assert 'https://us.i.posthog.com' in csp

    def test_empty_web_host_omits_wildcard(
        self, client: TestClient, clear_csp_env: None
    ) -> None:
        response = client.get('/sample')

        csp = _csp(response)
        # No trailing whitespace / leftover placeholder when WEB_HOST is unset.
        assert "frame-src 'self';" in csp
        assert "frame-src 'self' ;" not in csp
        assert 'https://*.' not in csp

    def test_single_label_web_host_omits_wildcard(
        self,
        client: TestClient,
        clear_csp_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``localhost`` has no registrable domain to wildcard."""
        monkeypatch.setenv('WEB_HOST', 'localhost')

        response = client.get('/sample')

        csp = _csp(response)
        assert 'https://*.' not in csp
        assert "frame-src 'self';" in csp

    def test_policy_override_takes_precedence(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom = "default-src 'none'"
        monkeypatch.setenv('CONTENT_SECURITY_POLICY_REPORT_ONLY', custom)

        response = client.get('/sample')

        assert _csp(response) == custom
