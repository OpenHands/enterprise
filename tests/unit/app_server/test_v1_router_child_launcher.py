"""The child-launch route is included only when openhands-sdk provides it."""

import importlib
import sys

import pytest

from openhands.app_server import v1_router

CHILDREN_PATH = '/api/v1/webhooks/conversations/{conversation_id}/children'
ROUTER_MODULE = 'openhands.app_server.event_callback.child_conversation_router'


def _paths(module) -> set[str]:
    return {getattr(route, 'path', None) for route in module.router.routes}


def test_app_boots_without_the_child_launch_contract(monkeypatch):
    """A pin that predates the contract leaves the route out, nothing else."""
    monkeypatch.setitem(sys.modules, ROUTER_MODULE, None)
    try:
        reloaded = importlib.reload(v1_router)
        paths = _paths(reloaded)
        assert CHILDREN_PATH not in paths
        assert '/api/v1/webhooks/conversations' in paths
    finally:
        monkeypatch.undo()
        importlib.reload(v1_router)


def test_child_launch_route_is_mounted_when_available():
    pytest.importorskip(ROUTER_MODULE)

    assert CHILDREN_PATH in _paths(v1_router)
