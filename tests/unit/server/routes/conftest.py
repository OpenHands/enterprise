"""Shared fixtures for route-level integration tests.

These tests exercise FastAPI routes that go through ``require_permission``.
Most of them only stub ``get_user_org_role`` and predate the user-scoped
"super" role lookup, so without help they hit an unpatched
``UserStore.get_user_by_id`` against a bare in-memory DB and explode with
``no such table: user``. The autouse fixture below defaults the super-role
lookup to ``None`` for every test in this directory. Tests that want to
exercise super-role behavior can stack their own ``patch`` over it.

``require_permission`` also looks the target org up to enforce suspension;
that lookup is defaulted to "not suspended" the same way.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _default_no_super_role():
    """Default ``get_user_super_role`` to return ``None`` for all route tests."""
    with patch(
        'server.auth.authorization.get_user_super_role',
        AsyncMock(return_value=None),
    ):
        yield


@pytest.fixture(autouse=True)
def _default_org_not_suspended():
    """Default ``is_org_suspended`` to ``False`` for all route tests."""
    with patch(
        'server.auth.authorization.is_org_suspended',
        AsyncMock(return_value=False),
    ):
        yield
