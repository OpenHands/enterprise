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

    # Routes that mirror FastAPI's built-in docs endpoints. We don't
    # need the real Swagger UI / ReDoc HTML for the middleware tests —
    # the middleware only cares about the path — so a tiny stub is
    # enough to exercise the CSP-skip logic.
    @app.get('/docs')
    def docs() -> dict[str, str]:
        return {'docs': 'stub'}

    @app.get('/docs/oauth2-redirect')
    def docs_redirect() -> dict[str, str]:
        return {'docs': 'redirect'}

    @app.get('/redoc')
    def redoc() -> dict[str, str]:
        return {'redoc': 'stub'}

    @app.get('/openapi.json')
    def openapi_json() -> dict[str, str]:
        return {'openapi': 'stub'}

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def clear_csp_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for var in (
        'CONTENT_SECURITY_POLICY',
        'WEB_HOST',
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def _csp(response) -> str:
    return response.headers['Content-Security-Policy']


class TestSecurityHeadersMiddleware:
    def test_sets_csp_header(self, client: TestClient, clear_csp_env: None) -> None:
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

    def test_no_report_only_header(
        self, client: TestClient, clear_csp_env: None
    ) -> None:
        response = client.get('/sample')

        # Enforce mode: the report-only header must NOT be present.
        assert 'Content-Security-Policy-Report-Only' not in response.headers
        assert 'Content-Security-Policy' in response.headers

    def test_header_applied_to_static_paths(
        self, client: TestClient, clear_csp_env: None
    ) -> None:
        response = client.get('/assets/sample.js')

        assert response.status_code == 200
        assert 'Content-Security-Policy' in response.headers

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

    def test_default_csp_skipped_on_docs(
        self, client: TestClient, clear_csp_env: None
    ) -> None:
        """FastAPI's /docs (Swagger UI) HTML loads its CSS and JS from
        cdn.jsdelivr.net, which the default policy does not allowlist. The
        default CSP must therefore be omitted on /docs, or the browser
        blocks the docs page entirely.
        """
        response = client.get('/docs')

        assert response.status_code == 200
        assert 'Content-Security-Policy' not in response.headers
        # The companion security headers must still be set: only the
        # CSP itself is exempt on the docs paths.
        assert response.headers['X-Content-Type-Options'] == 'nosniff'
        assert response.headers['X-Frame-Options'] == 'SAMEORIGIN'
        assert 'interest-cohort=()' in response.headers['Permissions-Policy']

    def test_default_csp_skipped_on_redoc(
        self, client: TestClient, clear_csp_env: None
    ) -> None:
        """Same exemption applies to /redoc (ReDoc), which also loads
        from cdn.jsdelivr.net.
        """
        response = client.get('/redoc')

        assert response.status_code == 200
        assert 'Content-Security-Policy' not in response.headers
        assert response.headers['X-Frame-Options'] == 'SAMEORIGIN'

    def test_default_csp_skipped_on_docs_subpaths(
        self, client: TestClient, clear_csp_env: None
    ) -> None:
        """The skip is path-prefix based so sub-paths like FastAPI's
        /docs/oauth2-redirect are also exempt.
        """
        response = client.get('/docs/oauth2-redirect')

        assert response.status_code == 200
        assert 'Content-Security-Policy' not in response.headers

    def test_default_csp_set_on_openapi_json(
        self, client: TestClient, clear_csp_env: None
    ) -> None:
        """``/openapi.json`` is plain JSON, not HTML, and is not in the
        skip list — the default CSP must still apply.
        """
        response = client.get('/openapi.json')

        assert response.status_code == 200
        assert 'Content-Security-Policy' in response.headers

    def test_path_that_only_starts_with_docs_letter_is_not_skipped(
        self, client: TestClient, clear_csp_env: None
    ) -> None:
        """The prefix match must anchor on a path boundary, not just on
        a substring. ``/docs-v2`` should not be exempt.
        """
        # Add an ad-hoc route to exercise the boundary check without
        # touching the shared fixture.
        from fastapi import FastAPI as _FastAPI

        local_app = _FastAPI()
        local_app.add_middleware(SecurityHeadersMiddleware)

        @local_app.get('/docs-v2')
        def docs_v2() -> dict[str, str]:
            return {'docs': 'v2'}

        local_client = TestClient(local_app)
        response = local_client.get('/docs-v2')

        assert response.status_code == 200
        assert 'Content-Security-Policy' in response.headers

    def test_csp_override_still_applies_to_docs(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Operators can opt back in to a strict policy on the docs
        paths by setting ``CONTENT_SECURITY_POLICY`` explicitly.
        """
        custom = "default-src 'none'"
        monkeypatch.setenv('CONTENT_SECURITY_POLICY', custom)

        response = client.get('/docs')

        assert response.headers['Content-Security-Policy'] == custom

    def test_csp_kill_switch_still_works_on_docs(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The empty-value kill switch (``CONTENT_SECURITY_POLICY=""``)
        must still disable CSP on the docs paths, same as everywhere
        else.
        """
        monkeypatch.setenv('CONTENT_SECURITY_POLICY', '')

        response = client.get('/docs')

        assert 'Content-Security-Policy' not in response.headers

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
        monkeypatch.setenv('CONTENT_SECURITY_POLICY', custom)

        response = client.get('/sample')

        assert _csp(response) == custom

    def test_explicit_empty_override_disables_csp(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Operators can set CONTENT_SECURITY_POLICY="" to disable CSP
        entirely in an emergency without redeploying.
        """
        monkeypatch.setenv('CONTENT_SECURITY_POLICY', '')

        response = client.get('/sample')

        assert 'Content-Security-Policy' not in response.headers
