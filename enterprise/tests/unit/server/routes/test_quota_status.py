"""Route-level tests for the quota status endpoint.

The point of interest is org scoping: the route must hand the service the
request's *effective* org (``X-Org-Id`` / API-key binding, resolved by
``EFFECTIVE_ORG_ID``) rather than letting the service fall back to the
user's last-selected ``current_org_id``.
"""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from server.auth.org_context import resolve_effective_org_id
from server.routes.quota import quota_router
from server.services.daily_conversation_quota_service import QuotaStatus

from openhands.app_server.user_auth import get_user_id

CALLER_USER_ID = str(uuid.uuid4())
EFFECTIVE_ORG = uuid.uuid4()


@asynccontextmanager
async def _session():
    """Session whose User lookup returns a user with no work email."""
    session = AsyncMock()
    session.scalar = AsyncMock(
        return_value=SimpleNamespace(work_email=None, work_email_verified_at=None)
    )
    yield session


@pytest.fixture
def mock_app():
    app = FastAPI()
    app.include_router(quota_router)
    app.dependency_overrides[get_user_id] = lambda: CALLER_USER_ID
    app.dependency_overrides[resolve_effective_org_id] = lambda: EFFECTIVE_ORG
    return app


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


@pytest.mark.asyncio
async def test_status_is_scoped_to_the_effective_org(mock_app):
    status = QuotaStatus(
        daily_limit=15,
        used_today=3,
        remaining=12,
        reset_at='2026-08-26T00:00:00+00:00',
    )
    get_status = AsyncMock(return_value=status)

    with (
        patch('server.routes.quota.a_session_maker', lambda **kwargs: _session()),
        patch(
            'server.routes.quota.DailyConversationQuotaService.get_status',
            get_status,
        ),
        patch(
            'server.routes.quota.QuotaIncreaseRequestService.get_latest_for_user',
            AsyncMock(return_value=None),
        ),
    ):
        async with _client(mock_app) as client:
            resp = await client.get('/api/quota/status')

    assert resp.status_code == 200
    assert resp.json() == {
        'daily_limit': 15,
        'used_today': 3,
        'remaining': 12,
        'reset_at': '2026-08-26T00:00:00+00:00',
        'work_email': None,
        'work_email_verified': False,
        'latest_request_status': None,
        'latest_request_requested_limit': None,
    }
    # The effective org -- not the user's current_org_id -- reaches the service.
    get_status.assert_awaited_once_with(CALLER_USER_ID, EFFECTIVE_ORG)


@pytest.mark.asyncio
async def test_status_reports_unlimited_without_a_limit(mock_app):
    status = QuotaStatus(
        daily_limit=None,
        used_today=7,
        remaining=None,
        reset_at='2026-08-26T00:00:00+00:00',
    )

    with (
        patch('server.routes.quota.a_session_maker', lambda **kwargs: _session()),
        patch(
            'server.routes.quota.DailyConversationQuotaService.get_status',
            AsyncMock(return_value=status),
        ),
        patch(
            'server.routes.quota.QuotaIncreaseRequestService.get_latest_for_user',
            AsyncMock(return_value=None),
        ),
    ):
        async with _client(mock_app) as client:
            resp = await client.get('/api/quota/status')

    assert resp.status_code == 200
    body = resp.json()
    assert body['daily_limit'] is None
    assert body['remaining'] is None
    assert body['used_today'] == 7
