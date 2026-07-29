"""Tests for OHE-2815 security headers and CSP report endpoint."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.app_server.middleware import SecurityHeadersMiddleware
from openhands.app_server.security.security_router import router as security_router


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

    app.include_router(security_router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def clear_csp_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for var in (
        'CONTENT_SECURITY_POLICY_REPORT_ONLY',
        'CONTENT_SECURITY_POLICY',
        'OH_FRAME_SRC_ALLOWLIST',
        'CONTENT_SECURITY_POLICY_REPORT_URI',
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
        assert 'report-uri /api/v1/security/csp-report' in csp

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

    def test_frame_src_allowlist_appended(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            'OH_FRAME_SRC_ALLOWLIST',
            'runtime.staging.all-hands.dev, .runtime.example.com',
        )

        response = client.get('/sample')

        csp = _csp(response)
        assert "frame-src 'self'" in csp
        assert 'runtime.staging.all-hands.dev' in csp
        assert '.runtime.example.com' in csp

    def test_empty_frame_src_allowlist_omits_extras(
        self, client: TestClient, clear_csp_env: None
    ) -> None:
        response = client.get('/sample')

        # No trailing whitespace when no extras are configured.
        assert "frame-src 'self';" in _csp(response)
        assert "frame-src 'self' ;" not in _csp(response)

    def test_policy_override_takes_precedence(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom = "default-src 'none'; report-uri /custom"
        monkeypatch.setenv('CONTENT_SECURITY_POLICY_REPORT_ONLY', custom)

        response = client.get('/sample')

        assert _csp(response) == custom

    def test_report_uri_override(
        self,
        client: TestClient,
        clear_csp_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            'CONTENT_SECURITY_POLICY_REPORT_URI', '/api/v1/custom-report'
        )

        response = client.get('/sample')

        assert 'report-uri /api/v1/custom-report' in _csp(response)


class TestCspReportEndpoint:
    def test_legacy_report_returns_204(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        body = {
            'csp-report': {
                'document-uri': 'https://example.test/page',
                'violated-directive': "script-src 'self'",
                'blocked-uri': 'https://evil.test/x.js',
                'original-policy': "default-src 'self'",
            }
        }
        with caplog.at_level(
            logging.INFO, logger='openhands.app_server.security.security_router'
        ):
            response = client.post(
                '/api/v1/security/csp-report',
                json=body,
                headers={'Content-Type': 'application/csp-report'},
            )

        assert response.status_code == 204
        assert any('CSP report:' in rec.message for rec in caplog.records)
        assert any("script-src 'self'" in rec.message for rec in caplog.records)

    def test_reporting_api_format(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        body = [
            {
                'type': 'csp-violation',
                'age': 0,
                'url': 'https://example.test/page',
                'body': {
                    'violated_directive': "img-src 'self'",
                    'blockedURL': 'https://tracker.test/pixel.png',
                },
            }
        ]
        with caplog.at_level(
            logging.INFO, logger='openhands.app_server.security.security_router'
        ):
            response = client.post(
                '/api/v1/security/csp-report',
                json=body,
                headers={'Content-Type': 'application/reports+json'},
            )

        assert response.status_code == 204
        assert any('img-src' in rec.message for rec in caplog.records)

    def test_invalid_json_returns_204(self, client: TestClient) -> None:
        response = client.post(
            '/api/v1/security/csp-report',
            content=b'not-json',
            headers={'Content-Type': 'application/csp-report'},
        )

        assert response.status_code == 204

    def test_unknown_content_type_still_logged(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        body = {
            'csp-report': {
                'document-uri': 'https://example.test/page',
                'violated-directive': "frame-ancestors 'self'",
                'blocked-uri': 'https://attacker.test',
            }
        }
        with caplog.at_level(
            logging.INFO, logger='openhands.app_server.security.security_router'
        ):
            response = client.post(
                '/api/v1/security/csp-report',
                json=body,
                headers={'Content-Type': 'application/json'},
            )

        assert response.status_code == 204
        assert any('frame-ancestors' in rec.message for rec in caplog.records)
