"""Exercise the actual route tree, authorization and public budget contracts."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from openhands.app_server.user_auth import get_user_id
from server.routes.budget_control import get_budget_controller
from server.routes.orgs import org_router
from server.services.budget_adoption_plan import BudgetAdoptionUnsupported
from server.services.budget_adoption_service import BudgetOperationNotFound
from server.services.managed_budget_service import ManagedBudgetService
from storage.budget_control import BudgetControlConflict, BudgetWriteDenied
from tests.unit.test_budget_adoption_service import Proxy
from tests.unit.test_budget_adoption_service import adoption as adoption_fixture

adoption = adoption_fixture

ORG_ID, ACTOR_ID, OPERATION_ID = (UUID(int=value) for value in (1, 2, 3))
ADOPTION = {
    'preview_fingerprint': 'a' * 64,
    'idempotency_key': 'request-1',
    'current_team_allowance': 100,
    'current_default_member_allowance': 10,
    'future_monthly_limit': 700,
    'future_default_member_limit': 80,
    'reset_day': 1,
    'replace_native_reset_schedules': False,
}
POLICY = {
    'idempotency_key': 'request-2',
    'expected_generation': 1,
    'enabled': True,
    'current_cycle_team_allowance': 100,
    'current_cycle_default_member_allowance': 10,
    'future_monthly_limit': 700,
    'future_default_member_limit': 80,
    'reset_day': 1,
}
ENDPOINTS = [
    ('GET', '/adoption/preview', None),
    ('POST', '/adoption', ADOPTION),
    ('PATCH', '/policy', POLICY),
    ('GET', f'/operations/{OPERATION_ID}', None),
    ('POST', f'/operations/{OPERATION_ID}/retry', None),
    ('POST', '/handoff', {'expected_generation': 1}),
]


def operation_result(state='applied'):
    return {
        'operation_id': str(OPERATION_ID),
        'operation_generation': 1,
        'kind': 'adopt',
        'status': state,
        'control_mode': 'managed' if state == 'applied' else 'needs_adoption',
        'generation': 1,
        'error': 'https://sk-secret@native/internal' if state == 'pending' else None,
        'created_at': datetime.now(UTC),
        'finished_at': None,
        'current_allowances': {'team': 100, 'default_member': 10, 'members': {}},
        'future_policy': {
            'monthly_limit': 700,
            'default_user_monthly_limit': 80,
            'member_limits': {},
            'reset_day': 1,
        },
        'cycle_end_at': '2026-10-01T00:00:00+00:00',
        'team_block': {'initial': False, 'target': False, 'budget_owned': False},
        'verification': {'token': 'sk-secret'},
        'plan': {'native_token': 'sk-secret'},
    }


@pytest.fixture
async def route_client():
    app = FastAPI()
    app.include_router(org_router)
    controller = MagicMock(spec=ManagedBudgetService)
    app.dependency_overrides[get_budget_controller] = lambda: controller
    app.dependency_overrides[get_user_id] = lambda: str(ACTOR_ID)
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
        ) as client:
            yield client, controller, app, role, binding


async def call(client, method, path, body=None, **kwargs):
    return await client.request(
        method, f'/api/organizations/{ORG_ID}/budgets{path}', json=body, **kwargs
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('method,path,body', ENDPOINTS)
@pytest.mark.parametrize(
    'access,expected', [('anonymous', 401), ('member', 403), ('unrelated', 403)]
)
async def test_every_control_endpoint_requires_org_admin(
    route_client, method, path, body, access, expected
):
    client, controller, app, role, _ = route_client
    if access == 'anonymous':
        app.dependency_overrides[get_user_id] = lambda: None
    else:
        role.return_value = (
            SimpleNamespace(name='member') if access == 'member' else None
        )
    result = await call(client, method, path, body)
    assert result.status_code == expected, result.text
    assert not controller.mock_calls


@pytest.mark.asyncio
@pytest.mark.parametrize('method,path,body', ENDPOINTS)
@pytest.mark.parametrize('mismatch', ['header', 'api_key'])
async def test_control_actions_cannot_switch_organizations(
    route_client, method, path, body, mismatch
):
    client, controller, _, _, binding = route_client
    headers = {}
    if mismatch == 'header':
        headers['X-Org-Id'] = str(uuid4())
    else:
        binding.return_value = uuid4()
    result = await call(client, method, path, body, headers=headers)
    assert result.status_code == (400 if mismatch == 'header' else 403), result.text
    assert not controller.mock_calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'operation_status,expected', [('applied', 200), ('pending', 202)]
)
@pytest.mark.parametrize(
    'method,path,body,controller_method',
    [
        ('POST', '/adoption', ADOPTION, 'confirm'),
        ('PATCH', '/policy', POLICY, 'update'),
        ('GET', f'/operations/{OPERATION_ID}', None, 'get_operation'),
        ('POST', f'/operations/{OPERATION_ID}/retry', None, 'retry'),
    ],
)
async def test_operation_responses_report_pending_and_filter_native_data(
    route_client, method, path, body, controller_method, operation_status, expected
):
    client, controller, _, role, _ = route_client
    action = getattr(controller, controller_method)
    action.return_value = operation_result(operation_status)
    result = await call(client, method, path, body)
    assert result.status_code == expected, result.text
    assert result.headers['cache-control'] == 'no-store'
    assert result.json()['status'] == operation_status
    assert result.json()['current_allowances']['team'] == 100
    assert result.json()['future_policy']['monthly_limit'] == 700
    assert 'sk-secret' not in result.text
    assert 'verification' not in result.json() and 'plan' not in result.json()
    role.assert_awaited_once_with(str(ACTOR_ID), ORG_ID)
    args = action.await_args.args
    assert args[0] == ORG_ID
    if body:
        assert args[1] == str(ACTOR_ID)
        assert args[2].idempotency_key == body['idempotency_key']
    else:
        assert args[1] == OPERATION_ID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'error,expected,code',
    [
        (
            BudgetControlConflict('Budget policy changed'),
            409,
            'budget_control_conflict',
        ),
        (BudgetWriteDenied('Counter reset'), 409, 'budget_control_conflict'),
        (
            BudgetAdoptionUnsupported('Reset schedule unknown'),
            422,
            'budget_control_unsupported',
        ),
        (
            RuntimeError('https://sk-secret@native/internal'),
            503,
            'budget_control_unavailable',
        ),
    ],
)
async def test_control_error_mapping_is_actionable_and_does_not_leak_native_errors(
    route_client, error, expected, code
):
    client, controller, *_ = route_client
    controller.confirm.side_effect = error
    result = await call(client, 'POST', '/adoption', ADOPTION)
    assert result.status_code == expected, result.text
    assert result.json()['detail']['code'] == code
    assert 'sk-secret' not in result.text


@pytest.mark.asyncio
async def test_missing_operation_returns_404(route_client):
    client, controller, *_ = route_client
    controller.get_operation.side_effect = BudgetOperationNotFound()
    result = await call(client, 'GET', f'/operations/{OPERATION_ID}')
    assert result.status_code == 404


@pytest.mark.asyncio
async def test_handoff_passes_reviewed_generation_and_actor(route_client):
    client, controller, *_ = route_client
    controller.hand_off.return_value = {'control_mode': 'external', 'generation': 2}
    result = await call(client, 'POST', '/handoff', {'expected_generation': 1})
    assert result.status_code == 200, result.text
    assert result.json() == controller.hand_off.return_value
    controller.hand_off.assert_awaited_once_with(
        ORG_ID, str(ACTOR_ID), expected_generation=1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'body',
    [
        {},
        {'expected_generation': True},
        {'expected_generation': -1},
        {'expected_generation': 1, 'actor': 'another-admin'},
    ],
)
async def test_handoff_requires_valid_generation_and_rejects_injected_actor(
    route_client, body
):
    client, controller, *_ = route_client
    result = await call(client, 'POST', '/handoff', body)
    assert result.status_code == 422, result.text
    assert not controller.mock_calls


@pytest.mark.asyncio
async def test_preview_exposes_caps_counters_and_restrictions_without_native_records(
    route_client,
):
    client, controller, *_ = route_client
    user_id = str(ACTOR_ID)
    native = Proxy(user_id).state
    native['default_member_budget'] = {
        'token': 'sk-secret',
        'metadata': {'secret': 'sk-secret'},
    }
    native['control_policy']['team'].update(
        soft_budget=5, model_max_budget={'model': 8}, budget_limits=[{'max_budget': 7}]
    )
    native['control_policy']['keys'] = [
        {
            'user_id': user_id,
            'identity': 'native-hash',
            'token': 'sk-secret',
            'metadata': {'secret': 'sk-secret'},
            'max_budget': 9,
            'blocked': True,
        }
    ]
    controller.preview.return_value = {
        'control_mode': 'needs_adoption',
        'generation': 0,
        'fingerprint': 'a' * 64,
        'observed_at': datetime.now(UTC),
        'pending_operation_id': None,
        'org_member_ids': [user_id],
        'observation': native,
    }
    result = await call(client, 'GET', '/adoption/preview')
    assert result.status_code == 200, result.text
    assert result.headers['cache-control'] == 'no-store'
    data = result.json()
    assert data['team_spend'] == 40
    assert data['team_policy']['max_budget'] == 500
    assert data['team_policy']['soft_budget'] == 5
    assert data['team_policy']['has_model_budgets']
    assert data['team_policy']['has_additional_budget_windows']
    assert data['members'][0]['enforcement_spend'] == 12
    assert data['members'][0]['max_budget'] == 25
    assert data['keys'][0]['policy']['blocked']
    assert data['keys'][0]['policy']['max_budget'] == 9
    assert not any(
        secret in result.text for secret in ['sk-secret', 'native-hash', 'metadata']
    )
    controller.preview.assert_awaited_once_with(ORG_ID)


@pytest.mark.asyncio
async def test_http_adoption_preserves_durable_intent_after_lost_response(
    route_client, adoption
):
    client, _, app, _, _ = route_client
    org_id, _, service, request, proxy = adoption
    app.dependency_overrides[get_budget_controller] = lambda: service
    url = f'/api/organizations/{org_id}/budgets'
    preview = await client.get(f'{url}/adoption/preview')
    assert preview.status_code == 200, preview.text
    request.preview_fingerprint = preview.json()['fingerprint']
    proxy.lose_next_response = True
    pending = await client.post(f'{url}/adoption', json=request.model_dump(mode='json'))
    assert pending.status_code == 202, pending.text
    pending_id = pending.json()['operation_id']
    assert pending.json()['control_mode'] == 'needs_adoption'
    proxy.state['team_spend'] += 5
    retried = await client.post(f'{url}/operations/{pending_id}/retry')
    assert retried.status_code == 200, retried.text
    assert retried.json()['status'] == 'applied'
    assert retried.json()['operation_id'] == pending_id
    assert retried.json()['generation'] == 1
    assert proxy.state['team_max_budget'] == 140
    reads, writes = proxy.observations, len(proxy.writes)
    repeated = await client.post(
        f'{url}/adoption', json=request.model_dump(mode='json')
    )
    assert repeated.json() == retried.json()
    assert (proxy.observations, len(proxy.writes)) == (reads, writes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'method,path,body',
    [
        ('POST', '/adoption', {**ADOPTION, 'preview_fingerprint': 'x' * 64}),
        ('POST', '/adoption', {**ADOPTION, 'actor': 'another-admin'}),
        ('POST', '/adoption', {**ADOPTION, 'current_team_allowance': -1}),
        ('PATCH', '/policy', {**POLICY, 'expected_generation': True}),
        ('PATCH', '/policy', {**POLICY, 'actor': 'another-admin'}),
        ('PATCH', '/policy', {**POLICY, 'current_cycle_team_allowance': -1}),
    ],
)
async def test_invalid_policy_requests_are_rejected_before_controller(
    route_client, method, path, body
):
    client, controller, *_ = route_client
    result = await call(client, method, path, body)
    assert result.status_code == 422, result.text
    assert not controller.mock_calls
