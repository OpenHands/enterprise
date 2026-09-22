from __future__ import annotations

from fastapi.routing import APIRoute

from integrations_hub.main import app
from integrations_hub.route_manifest import ROUTES


IGNORED_METHODS = {"HEAD", "OPTIONS"}


def manifest_routes() -> set[tuple[str, str]]:
    return set(ROUTES)


def fastapi_routes() -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        for method in route.methods - IGNORED_METHODS:
            routes.add((method, route.path))
    return routes


def test_fastapi_backend_matches_manifest_route_surface() -> None:
    assert len(ROUTES) == 67
    assert manifest_routes() - fastapi_routes() == set()
