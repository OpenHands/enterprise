"""Provisioning reads exact native identity and never treats failure as absence."""

import json
from copy import deepcopy

import httpx
import pytest

from storage.budget_control import BudgetWriteDenied
from storage.lite_llm_manager import LiteLlmManager


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(
        'storage.lite_llm_manager.LITE_LLM_API_URL', 'http://proxy.test'
    )
    monkeypatch.setattr('storage.lite_llm_manager.LITE_LLM_API_KEY', 'test-master')


async def create(client, kind):
    if kind == 'team':
        return await LiteLlmManager._create_team(client, 'new alias', 'identity', 100)
    return await LiteLlmManager._create_user(client, 'user@example.test', 'identity')


def member_team():
    return {
        'team_info': {
            'team_id': 'team',
            'members_with_roles': [{'user_id': 'other', 'role': 'admin'}],
        },
        'team_memberships': [{'user_id': 'other', 'team_id': 'team', 'spend': 7}],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('source', ['roster', 'counter', 'both'])
@pytest.mark.parametrize('requested_budget', [None, 0, 1000])
async def test_existing_membership_is_read_only(source, requested_budget):
    native = member_team()
    if source in {'roster', 'both'}:
        native['team_info']['members_with_roles'].append(
            {'user_id': 'member', 'role': 'admin'}
        )
    if source in {'counter', 'both'}:
        native['team_memberships'].append(
            {
                'user_id': 'member',
                'team_id': 'team',
                'spend': 80,
                'budget_id': 'private',
                'litellm_budget_table': {
                    'max_budget': 0,
                    'rpm_limit': 7,
                    'budget_duration': '1d',
                },
            }
        )
    before = deepcopy(native)
    calls = []

    async def handler(request):
        calls.append(request)
        assert request.method == 'GET'
        return httpx.Response(200, json=native)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await LiteLlmManager._add_user_to_team(
            client, 'member', 'team', requested_budget
        )
    assert len(calls) == 1
    assert native == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'failure',
    [
        401,
        403,
        404,
        500,
        'foreign',
        'missing_roster',
        'missing_counters',
        'foreign_counter',
    ],
)
async def test_unverified_membership_never_authorizes_addition(failure):
    native = member_team()
    if failure == 'foreign':
        native['team_info']['team_id'] = 'another-team'
    elif failure == 'missing_roster':
        del native['team_info']['members_with_roles']
    elif failure == 'missing_counters':
        del native['team_memberships']
    elif failure == 'foreign_counter':
        native['team_memberships'][0]['team_id'] = 'another-team'

    async def handler(request):
        assert request.method == 'GET'
        return httpx.Response(failure if isinstance(failure, int) else 200, json=native)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises((httpx.HTTPError, BudgetWriteDenied)):
            await LiteLlmManager._add_user_to_team(client, 'member', 'team', 100)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'outcome', ['success', 'lost_response', 'duplicate', 'no_effect']
)
async def test_member_creation_requires_readback_and_retries_without_another_post(
    outcome,
):
    native = member_team()
    writes = []

    async def handler(request):
        if request.method == 'GET':
            return httpx.Response(200, json=native)
        assert request.url.path == '/team/member_add'
        writes.append(json.loads(request.content))
        if outcome != 'no_effect':
            native['team_info']['members_with_roles'].append(
                {'user_id': 'member', 'role': 'user'}
            )
        if outcome == 'lost_response':
            raise httpx.ReadTimeout('response lost after real creation')
        if outcome == 'duplicate':
            return httpx.Response(400, json={'error': 'User already in team'})
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        if outcome in {'no_effect', 'lost_response'}:
            with pytest.raises((BudgetWriteDenied, httpx.ReadTimeout)):
                await LiteLlmManager._add_user_to_team(client, 'member', 'team', 100)
        else:
            await LiteLlmManager._add_user_to_team(client, 'member', 'team', 100)
        if outcome != 'no_effect':
            await LiteLlmManager._add_user_to_team(client, 'member', 'team', 1000)
        assert len(writes) == 1
        assert writes[0]['max_budget_in_team'] == 100


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['team', 'user'])
async def test_existing_identity_is_read_only_even_with_independent_zero_policy(kind):
    native = {
        f'{kind}_id': 'identity',
        'spend': 5,
        'max_budget': 0,
        'models': ['only-approved'],
        'metadata': {'operator': 'preserve'},
    }
    before = deepcopy(native)
    calls = []

    async def handler(request):
        calls.append(request)
        assert request.method == 'GET'
        assert request.url.params[f'{kind}_id'] == 'identity'
        return httpx.Response(200, json={f'{kind}_info': native})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await create(client, kind)
    assert native == before
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['team', 'user'])
@pytest.mark.parametrize('failure', [401, 403, 500, 'malformed', 'foreign', 'timeout'])
async def test_unverified_identity_read_never_authorizes_creation(kind, failure):
    calls = []

    async def handler(request):
        calls.append(request)
        assert request.method == 'GET'
        if failure == 'timeout':
            raise httpx.ReadTimeout('unavailable')
        if isinstance(failure, int):
            return httpx.Response(failure)
        return httpx.Response(
            200,
            json={
                f'{kind}_info': None
                if failure == 'malformed'
                else {f'{kind}_id': 'foreign'}
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        if kind == 'user':
            assert await create(client, kind) is False
        else:
            with pytest.raises((httpx.HTTPError, BudgetWriteDenied)):
                await create(client, kind)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['team', 'user'])
@pytest.mark.parametrize('lose_response', [False, True])
async def test_creation_and_lost_response_retry_use_same_identity_once(
    kind, lose_response
):
    native = None
    writes = []

    async def handler(request):
        nonlocal native
        if request.method == 'GET':
            return (
                httpx.Response(404)
                if native is None
                else httpx.Response(200, json={f'{kind}_info': native})
            )
        assert request.url.path == f'/{kind}/new'
        body = json.loads(request.content)
        writes.append(body)
        native = {f'{kind}_id': body[f'{kind}_id']}
        if lose_response:
            raise httpx.ReadTimeout('response lost')
        return httpx.Response(200, json=native)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        if lose_response and kind == 'team':
            with pytest.raises(httpx.ReadTimeout):
                await create(client, kind)
        else:
            first = await create(client, kind)
            if kind == 'user':
                assert first is (not lose_response)
        await create(client, kind)
    assert len(writes) == 1
    assert writes[0][f'{kind}_id'] == 'identity'


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [400, 409])
async def test_email_collision_retries_without_email_and_verifies_identity(status):
    native = None
    writes = []

    async def handler(request):
        nonlocal native
        if request.method == 'GET':
            return (
                httpx.Response(404)
                if native is None
                else httpx.Response(200, json={'user_info': native})
            )
        body = json.loads(request.content)
        writes.append(body)
        if len(writes) == 1:
            return httpx.Response(
                status,
                json={'error': 'User with email user@example.test already exists'},
            )
        native = {'user_id': 'identity'}
        return httpx.Response(200, json=native)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await create(client, 'user') is True
    assert len(writes) == 2
    assert writes[0]['user_email'] == 'user@example.test'
    assert writes[1] == {**writes[0], 'user_email': None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'status,message',
    [
        (500, 'duplicate email'),
        (403, 'User with email already exists'),
        (400, 'Invalid team'),
        (409, 'User with id identity already exists'),
    ],
)
async def test_arbitrary_creation_error_never_triggers_second_write(status, message):
    writes = []

    async def handler(request):
        if request.method == 'GET':
            return httpx.Response(404)
        writes.append(request)
        return httpx.Response(status, json={'error': message})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await create(client, 'user') is False
    assert len(writes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['team', 'user'])
async def test_success_response_without_verified_entity_is_not_success(kind):
    async def handler(request):
        return (
            httpx.Response(404)
            if request.method == 'GET'
            else httpx.Response(200, json={})
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        if kind == 'user':
            assert await create(client, kind) is False
        else:
            with pytest.raises(BudgetWriteDenied, match='pending verification'):
                await create(client, kind)
