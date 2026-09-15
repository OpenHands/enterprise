"""The alert-only endpoints retain the same organization permission boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from openhands.app_server.user_auth import get_user_id
from server.routes.budget_control import get_budget_notifications
from server.routes.orgs import org_router
from server.services.budget_notification_service import (
    BudgetNotificationService,
    BudgetNotificationState,
)
from storage.budget_control import BudgetControlConflict

ORG, ACTOR = uuid4(), uuid4()
STATE = BudgetNotificationState(
    thresholds=[],
    slack_channel=None,
    slack_team_id=None,
    fingerprint='a' * 64,
    email_configured=False,
    slack_account_linked=False,
)
BODY = {'expected_fingerprint': 'a' * 64, 'thresholds': []}


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(org_router)
    service = AsyncMock(spec=BudgetNotificationService)
    service.get.return_value = STATE
    service.update.return_value = STATE
    app.dependency_overrides[get_budget_notifications] = lambda: service
    app.dependency_overrides[get_user_id] = lambda: str(ACTOR)
    with (
        patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=SimpleNamespace(name='admin')),
        ) as role,
        patch(
            'server.auth.authorization.get_api_key_org_id_from_request',
            AsyncMock(return_value=None),
        ) as binding,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://test'
        ) as http:
            yield http, service, app, role, binding


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['GET', 'PUT'])
@pytest.mark.parametrize(
    'access', ['anonymous', 'member', 'unrelated', 'header', 'api_key', 'admin']
)
async def test_alert_endpoints_are_org_scoped(client, method, access):
    http, service, app, role, binding = client
    headers = {}
    expected = 403
    if access == 'anonymous':
        app.dependency_overrides[get_user_id] = lambda: None
        expected = 401
    elif access == 'member':
        role.return_value = SimpleNamespace(name='member')
    elif access == 'unrelated':
        role.return_value = None
    elif access == 'header':
        headers['X-Org-Id'] = str(uuid4())
        expected = 400
    elif access == 'api_key':
        binding.return_value = uuid4()
    else:
        expected = 200
    response = await http.request(
        method,
        f'/api/organizations/{ORG}/budgets/notifications',
        json=BODY if method == 'PUT' else None,
        headers=headers,
    )
    assert response.status_code == expected, response.text
    if expected != 200:
        assert not service.mock_calls
    else:
        assert response.headers['cache-control'] == 'no-store'
        action = service.update if method == 'PUT' else service.get
        assert action.await_args.args[:2] == (ORG, str(ACTOR))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'field',
    ['monthly_limit', 'control_mode', 'enabled', 'current_cycle_team_allowance'],
)
async def test_alert_endpoint_rejects_financial_fields(client, field):
    http, service, *_ = client
    response = await http.put(
        f'/api/organizations/{ORG}/budgets/notifications', json=BODY | {field: 100}
    )
    assert response.status_code == 422
    service.update.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'error,expected',
    [
        (RuntimeError('xoxb-private-secret'), 503),
        (BudgetControlConflict('Alert preferences changed'), 409),
    ],
)
async def test_alert_errors_do_not_expose_bot_credentials(client, error, expected):
    http, service, *_ = client
    service.update.side_effect = error
    response = await http.put(
        f'/api/organizations/{ORG}/budgets/notifications', json=BODY
    )
    assert response.status_code == expected
    assert 'private-secret' not in response.text
