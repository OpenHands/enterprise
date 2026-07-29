"""Behavioral tests for SecurityHeadersMiddleware.

The middleware attaches anti-clickjacking headers to every response so the
application cannot be embedded in a frame (CWE-1021). These tests confirm the
headers are present on both a JSON API route and a mounted static file, since
the pen-test remediation requires consistent coverage across all routes and
APIs.
"""

import os
import tempfile

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from server.middleware import SecurityHeadersMiddleware
from starlette.staticfiles import StaticFiles


def _build_client() -> TestClient:
    app = FastAPI()

    @app.get('/api/v1/thing')
    def thing():
        return {'ok': True}

    @app.get('/stream')
    def stream():
        def gen():
            yield b'chunk'

        return StreamingResponse(gen(), media_type='text/plain')

    static_dir = tempfile.mkdtemp()
    with open(os.path.join(static_dir, 'index.html'), 'w') as f:
        f.write('<!DOCTYPE html><html><body>hi</body></html>')
    app.mount('/', StaticFiles(directory=static_dir, html=True), name='dist')

    app.add_middleware(SecurityHeadersMiddleware)
    return TestClient(app)


class TestSecurityHeadersMiddleware:
    def test_api_response_has_anti_clickjacking_headers(self):
        client = _build_client()

        response = client.get('/api/v1/thing')

        assert response.status_code == 200
        assert response.headers['X-Frame-Options'] == 'DENY'
        assert response.headers['Content-Security-Policy'] == "frame-ancestors 'none'"

    def test_static_response_has_anti_clickjacking_headers(self):
        client = _build_client()

        response = client.get('/')

        assert response.status_code == 200
        assert response.headers['X-Frame-Options'] == 'DENY'
        assert response.headers['Content-Security-Policy'] == "frame-ancestors 'none'"

    def test_streaming_response_has_anti_clickjacking_headers(self):
        client = _build_client()

        response = client.get('/stream')

        assert response.status_code == 200
        assert response.headers['X-Frame-Options'] == 'DENY'
        assert response.headers['Content-Security-Policy'] == "frame-ancestors 'none'"
