"""The effect boundary accepts only the exact durable operation's payloads."""

import json
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest

from storage.budget_control import (
    BudgetWriteDenied,
    budget_control_session,
    budget_request_hash,
)
from storage.lite_llm_manager import LiteLlmManager
from storage.org_budget_operation import OrgBudgetOperation
from storage.org_budget_settings import OrgBudgetSettings


@pytest.fixture
async def budget_org(create_org, async_session_maker):
    org = create_org()
    async with async_session_maker() as session:
        session.add(OrgBudgetSettings(org_id=org.id, control_mode='needs_adoption'))
        await session.commit()
    return org.id


async def reserve(control):
    return await control.reserve_operation(
        idempotency_key='adopt',
        request_hash=budget_request_hash({'remaining': 100}),
        kind='adopt',
        actor='admin',
        plan={
            'writes': [
                {
                    'path': '/team/update',
                    'body': {
                        'team_id': str(control.org_id),
                        'max_budget': 140,
                        'budget_duration': None,
                        'budget_reset_at': None,
                    },
                }
            ],
            'team_baseline': 40,
        },
    )


@pytest.fixture(autouse=True)
def proxy_config():
    with (
        patch('storage.lite_llm_manager.LITE_LLM_API_URL', 'http://litellm.test'),
        patch('storage.lite_llm_manager.LITE_LLM_API_KEY', 'test-only'),
    ):
        yield


@pytest.mark.asyncio
async def test_write_arrives_only_after_intent_is_visible_to_other_connections(
    async_engine, async_session_maker, budget_org
):
    requests = []
    async with budget_control_session(async_engine, budget_org) as control:
        operation = await reserve(control)

        async def handle(request):
            async with async_session_maker() as reader:
                durable = await reader.get(OrgBudgetOperation, operation.id)
                assert durable.status == 'pending'
                assert durable.plan['team_baseline'] == 40
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            write = operation.plan['writes'][0]
            await LiteLlmManager._apply_budget_write(
                client, budget_org, operation.id, **write
            )
        assert requests == [write['body']]
        assert (await control.settings()).control_mode == 'needs_adoption'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['amount', 'org', 'endpoint', 'unblock', 'models'])
async def test_modified_payload_cannot_pass_boundary(async_engine, budget_org, change):
    async with budget_control_session(async_engine, budget_org) as control:
        operation = await reserve(control)
        write = operation.plan['writes'][0]
        body = dict(write['body'])
        path = write['path']
        if change == 'amount':
            body['max_budget'] = 190
        elif change == 'org':
            body['team_id'] = str(uuid4())
        elif change == 'endpoint':
            path = '/team/delete'
        elif change == 'unblock':
            body['blocked'] = False
        else:
            body['models'] = []

        def handle(request):
            pytest.fail('Denied mutation reached the network')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(BudgetWriteDenied):
                await LiteLlmManager._apply_budget_write(
                    client, budget_org, operation.id, path, body
                )


@pytest.mark.asyncio
async def test_retry_after_lost_response_resends_same_absolute_cap(
    async_engine, budget_org
):
    requests = []

    def lose_response(request):
        requests.append(json.loads(request.content))
        raise httpx.ReadTimeout('Response lost after LiteLLM applied the cap')

    async with budget_control_session(async_engine, budget_org) as control:
        operation = await reserve(control)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lose_response)
        ) as client:
            with pytest.raises(httpx.ReadTimeout):
                await LiteLlmManager._apply_budget_write(
                    client, budget_org, operation.id, **operation.plan['writes'][0]
                )

    def success(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={})

    async with budget_control_session(async_engine, budget_org) as control:
        retry = await control.pending_operation()
        async with httpx.AsyncClient(transport=httpx.MockTransport(success)) as client:
            await LiteLlmManager._apply_budget_write(
                client, budget_org, retry.id, **retry.plan['writes'][0]
            )
    assert requests[0] == requests[1]
    assert requests[1]['max_budget'] == 140


@pytest.mark.asyncio
async def test_handed_off_operation_cannot_write_even_with_exact_old_payload(
    async_engine, budget_org
):
    async with budget_control_session(async_engine, budget_org) as control:
        operation = await reserve(control)
        await control.hand_off('admin')
        with pytest.raises(BudgetWriteDenied):
            await control.authorize_write(operation.id, **operation.plan['writes'][0])
