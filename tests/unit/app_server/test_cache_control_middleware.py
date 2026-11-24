"""Tests for CacheControlMiddleware's fingerprinted-asset detection."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from openhands.app_server.middleware import CacheControlMiddleware


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(CacheControlMiddleware)

    @app.get('/assets/{name}')
    def top_level_asset(name: str) -> dict[str, str]:
        return {'name': name}

    @app.get('/canvas/assets/{name}')
    def canvas_asset(name: str) -> dict[str, str]:
        return {'name': name}

    @app.get('/canvas/index.html')
    def canvas_index() -> dict[str, str]:
        return {'ok': 'index'}

    @app.get('/api/v1/sample')
    def api_sample() -> dict[str, str]:
        return {'ok': 'yes'}

    @app.get('/api/v1/sandbox-proxy/oh-1/8001/static/{name}')
    def sandbox_file(name: str) -> Response:
        return Response(name, headers={'Cache-Control': 'public, max-age=31536000'})

    @app.get('/api/v1/sandbox-proxy/oh-1/8000/api/info')
    def sandbox_api() -> dict[str, str]:
        return {'ok': 'yes'}

    return TestClient(app)


@pytest.mark.parametrize(
    'path',
    [
        '/assets/app-abc123.js',
        '/canvas/assets/app-abc123.js',
        '/canvas/assets/vendor-def456.css',
    ],
)
def test_fingerprinted_assets_are_cached_aggressively(
    client: TestClient, path: str
) -> None:
    # Regression test: the Agent Canvas SPA serves its fingerprinted chunks
    # from /canvas/assets/*. A prior `startswith('/assets')` check here
    # missed that prefix, so the browser was told never to cache Canvas
    # JS/CSS and re-downloaded every chunk on each page load.
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'public, max-age=2592000, immutable'
    assert 'pragma' not in response.headers


@pytest.mark.parametrize('path', ['/canvas/index.html', '/api/v1/sample'])
def test_non_asset_paths_are_not_cached(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert (
        response.headers['cache-control']
        == 'no-cache, no-store, must-revalidate, max-age=0'
    )
    assert response.headers['pragma'] == 'no-cache'
    assert response.headers['expires'] == '0'


def test_sandbox_responses_keep_their_own_caching(client: TestClient) -> None:
    cached = client.get('/api/v1/sandbox-proxy/oh-1/8001/static/workbench.js')
    uncached = client.get('/api/v1/sandbox-proxy/oh-1/8000/api/info')

    assert cached.headers['cache-control'] == 'public, max-age=31536000'
    assert 'cache-control' not in uncached.headers
    assert 'pragma' not in uncached.headers
