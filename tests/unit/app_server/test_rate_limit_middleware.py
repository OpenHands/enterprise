"""Tests for RateLimitMiddleware's static-asset exemption."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

from openhands.app_server.middleware import InMemoryRateLimiter, RateLimitMiddleware


@pytest.fixture
def middleware() -> RateLimitMiddleware:
    # app arg is unused by is_rate_limited_request; None is fine here.
    return RateLimitMiddleware(app=None, rate_limiter=InMemoryRateLimiter())


@pytest.mark.parametrize(
    'path',
    [
        '/assets/app-abc123.js',
        '/canvas/assets/app-abc123.js',
        '/canvas/assets/vendor-def456.css',
    ],
)
def test_fingerprinted_assets_are_exempt(
    middleware: RateLimitMiddleware, path: str
) -> None:
    # Regression test: the Agent Canvas SPA serves its fingerprinted chunks
    # from /canvas/assets/*. A prior `startswith('/assets')` check here
    # missed that prefix, so every cold load of /canvas tripped the rate
    # limiter and picked up an artificial ~1s delay (or a 429) per file.
    request = StarletteRequest(
        scope={'type': 'http', 'method': 'GET', 'path': path, 'headers': []}
    )
    assert middleware.is_rate_limited_request(request) is False


def test_non_asset_paths_are_still_rate_limited(
    middleware: RateLimitMiddleware,
) -> None:
    request = StarletteRequest(
        scope={
            'type': 'http',
            'method': 'GET',
            'path': '/api/v1/conversations',
            'headers': [],
        }
    )
    assert middleware.is_rate_limited_request(request) is True


def test_canvas_index_html_is_still_rate_limited(
    middleware: RateLimitMiddleware,
) -> None:
    # Only the fingerprinted /assets/ sub-path is exempt; index.html itself
    # (served fresh on every load, see CacheControlMiddleware) is not.
    request = StarletteRequest(
        scope={
            'type': 'http',
            'method': 'GET',
            'path': '/canvas/index.html',
            'headers': [],
        }
    )
    assert middleware.is_rate_limited_request(request) is True


def test_canvas_assets_never_delayed_on_burst() -> None:
    """End-to-end: a burst of Canvas asset requests must not be throttled."""
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        # Tiny limit so a real cold-load-sized burst would trip it if the
        # assets weren't exempt.
        rate_limiter=InMemoryRateLimiter(requests=2, seconds=60, sleep_seconds=5),
    )

    @app.get('/canvas/assets/{name}')
    def asset(name: str) -> dict[str, str]:
        return {'name': name}

    client = TestClient(app)
    start = time.monotonic()
    for i in range(10):
        response = client.get(f'/canvas/assets/chunk-{i}.js')
        assert response.status_code == 200
    elapsed = time.monotonic() - start

    # If the exemption regressed, requests past the limit would each incur
    # the configured 5s sleep, so this bounds well under one sleep interval.
    assert elapsed < 2
