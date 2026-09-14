"""Credential creation uses the budget lock and preserves independent key policy."""

import asyncio
import contextvars
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from storage.budget_control import (
    BudgetControlConflict,
    BudgetWriteDenied,
    budget_control_session,
    current_budget_control,
)
from storage.lite_llm_manager import LiteLlmManager, get_openhands_cloud_key_alias
from storage.litellm_key_policy import key_mutation_scope, key_restrictions
from storage.org_budget_settings import OrgBudgetSettings


@pytest.fixture
async def key_org(create_org, async_session_maker):
    org_id = create_org().id
    async with async_session_maker() as session:
        session.add(OrgBudgetSettings(org_id=org_id, control_mode='external'))
        await session.commit()
    with (
        patch('storage.database.a_session_maker', async_session_maker),
        patch('storage.lite_llm_manager.LITE_LLM_API_KEY', 'test-master'),
        patch('storage.lite_llm_manager.LITE_LLM_API_URL', 'http://litellm.test'),
    ):
        yield org_id


def existing_key(org_id, **kwargs):
    return {
        'token': hashlib.sha256(b'sk-existing').hexdigest(),
        'user_id': 'user-1',
        'team_id': str(org_id),
        'key_alias': 'operator-alias',
        'spend': 15.0,
        'metadata': {'type': 'openhands'},
        **kwargs,
    }


def client_for(keys, *, alias_result=None, user_status=200):
    calls = []

    async def handle(request):
        calls.append(request)
        if request.url.path == '/user/info':
            return httpx.Response(user_status, json={'keys': keys})
        if request.url.path == '/key/list':
            assert request.url.params['size'] == '1'
            return httpx.Response(
                200, json=alias_result or {'keys': [], 'total_count': 0}
            )
        assert request.url.path == '/key/generate'
        return httpx.Response(200, json={'key': 'sk-generated'})

    return httpx.AsyncClient(transport=httpx.MockTransport(handle)), calls


@pytest.mark.parametrize(
    'field,value',
    [
        ('max_budget', 0),
        ('max_budget', 50),
        ('budget_id', 'native-budget'),
        ('budget_duration', '1d'),
        ('budget_reset_at', '2026-10-01T00:00:00Z'),
        ('models', ['approved']),
        ('model_max_budget', {'approved': 1}),
        ('blocked', True),
        ('rpm_limit', 0),
        ('tpm_limit', 100),
        ('max_parallel_requests', 1),
        ('expires', '2026-10-01T00:00:00Z'),
        ('permissions', {'deny': 'something'}),
        ('guardrails', ['required']),
        ('metadata', {'operator_policy': True}),
        ('future_policy', 'unknown'),
        ('key_type', 'read_only'),
        ('auto_rotate', True),
        ('max_budget', False),
        ('metadata', False),
        ('unknown_permission', False),
    ],
)
@pytest.mark.asyncio
async def test_repair_does_not_bypass_existing_key_constraints(key_org, field, value):
    client, calls = client_for([existing_key(key_org, **{field: value})])
    async with client:
        with pytest.raises(BudgetWriteDenied, match='Existing key policy'):
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), 'new-alias', None
            )
    assert all(call.method == 'GET' for call in calls)


def test_counter_observations_and_empty_policy_are_not_restrictions():
    assert (
        key_restrictions(
            existing_key(
                'org',
                models=[],
                max_budget=None,
                blocked=False,
                team_alias='Operator name',
                key_type='default',
                rotation_count=0,
            )
        )
        == []
    )


@pytest.mark.parametrize('mode', ['external', 'needs_adoption', 'managed'])
@pytest.mark.asyncio
async def test_new_unrestricted_identity_does_not_change_budget_ownership(
    key_org, async_session_maker, mode
):
    async with async_session_maker() as session:
        settings = await session.scalar(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == key_org)
        )
        settings.control_mode = mode
        settings.monthly_limit = 12
        await session.commit()
    client, calls = client_for([])
    async with client:
        assert (
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), 'new-alias', None
            )
            == 'sk-generated'
        )
    assert [call.url.path for call in calls] == [
        '/user/info',
        '/key/list',
        '/key/generate',
    ]
    async with async_session_maker() as session:
        settings = await session.scalar(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == key_org)
        )
        assert settings.control_mode == mode
        assert settings.control_generation == 0
        assert settings.monthly_limit == 12


@pytest.mark.parametrize(
    'alias_result',
    [
        {'keys': [{'user_id': 'another-user'}], 'total_count': 1},
        {'keys': [], 'total_count': 1},
        {'unrecognized': []},
        {'keys': []},
        {'keys': [], 'total_count': False},
        {'keys': [], 'total_count': -1},
    ],
)
@pytest.mark.asyncio
async def test_existing_or_unreadable_alias_is_never_deleted(key_org, alias_result):
    client, calls = client_for([], alias_result=alias_result)
    async with client:
        with pytest.raises(BudgetWriteDenied):
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), 'same-alias', None
            )
    assert all(call.method == 'GET' for call in calls)


@pytest.mark.parametrize(
    'keys,user_status', [(None, 200), ({}, 200), ([None], 200), ([], 503)]
)
@pytest.mark.asyncio
async def test_unavailable_or_malformed_key_policy_never_mints(
    key_org, keys, user_status
):
    client, calls = client_for(keys, user_status=user_status)
    async with client:
        with pytest.raises(BudgetWriteDenied, match='unavailable'):
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), 'new-alias', None
            )
    assert all(call.method == 'GET' for call in calls)


@pytest.mark.asyncio
async def test_pending_budget_operation_blocks_key_creation(key_org, async_engine):
    async with budget_control_session(async_engine, key_org) as control:
        await control.reserve_operation(
            idempotency_key='adopt',
            request_hash='test',
            kind='adopt',
            actor='admin',
            plan={},
        )
    client, calls = client_for([])
    async with client:
        with pytest.raises(BudgetControlConflict, match='pending budget'):
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), 'new-alias', None
            )
    assert calls == []


@pytest.mark.asyncio
async def test_credential_writer_and_adoption_use_same_cross_task_lock(
    key_org, async_engine
):
    async with key_mutation_scope(str(key_org)):
        current_budget_control(key_org).assert_locked()

        async def competitor():
            with pytest.raises(BudgetControlConflict):
                async with budget_control_session(async_engine, key_org):
                    pytest.fail('Concurrent adoption obtained credential writer lock')

        # A separate context models another worker without inheriting lock authority.
        await asyncio.create_task(competitor(), context=contextvars.Context())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'changes',
    [
        {'token': None},
        {'token': ''},
        {'user_id': 'another-user'},
        {'team_id': None, 'max_budget': 0},
    ],
)
async def test_unknown_key_ownership_does_not_allow_replacement(key_org, changes):
    client, calls = client_for([existing_key(key_org, **changes)])
    async with client:
        with pytest.raises(BudgetWriteDenied):
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), 'new-alias', None
            )
    assert all(call.method == 'GET' for call in calls)


@pytest.mark.asyncio
async def test_other_organization_constraints_do_not_block_new_identity(key_org):
    client, calls = client_for(
        [existing_key('another-org', max_budget=0, blocked=True)]
    )
    async with client:
        assert (
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), 'new-alias', None
            )
            == 'sk-generated'
        )
    assert len([call for call in calls if call.method == 'POST']) == 1


@pytest.mark.asyncio
async def test_cancellation_during_key_creation_releases_org_lock(
    key_org, async_engine
):
    reached_write = asyncio.Event()

    async def handle(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'keys': [], 'total_count': 0})
        current_budget_control(key_org).assert_locked()
        reached_write.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        task = asyncio.create_task(
            LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), 'new-alias', None
            )
        )
        await asyncio.wait_for(reached_write.wait(), timeout=5)
        with pytest.raises(BudgetControlConflict):
            async with budget_control_session(async_engine, key_org):
                pytest.fail('In-flight credential creation lost the org lock')
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with budget_control_session(async_engine, key_org):
        pass


@pytest.mark.asyncio
async def test_child_task_cannot_use_inherited_key_write_authority(key_org):
    async with key_mutation_scope(str(key_org)):

        async def child():
            with pytest.raises(BudgetWriteDenied):
                async with key_mutation_scope(str(key_org)):
                    pytest.fail('Child task reused parent key write authority')

        await asyncio.create_task(child())


@pytest.mark.asyncio
async def test_missing_organization_never_acquires_key_authority():
    with pytest.raises(BudgetWriteDenied, match='require an organization'):
        async with key_mutation_scope(None):
            pytest.fail('Missing organization allowed credential creation')


@pytest.mark.asyncio
async def test_key_authority_requires_an_async_database_engine():
    session = AsyncMock()
    session.bind = None
    session.__aenter__.return_value = session
    with patch('storage.database.a_session_maker', MagicMock(return_value=session)):
        with pytest.raises(BudgetWriteDenied, match='write authority'):
            async with key_mutation_scope('11111111-1111-1111-1111-111111111111'):
                pytest.fail('Invalid database binding allowed credential creation')


@pytest.mark.asyncio
async def test_key_authority_accepts_connection_bound_sessions(key_org, async_engine):
    async with async_engine.connect() as connection:
        with patch('storage.database.a_session_maker', async_sessionmaker(connection)):
            async with key_mutation_scope(str(key_org)):
                current_budget_control(key_org).assert_locked()


@pytest.mark.asyncio
async def test_response_loss_never_deletes_or_mints_a_second_credential(key_org):
    calls = []
    created = False
    alias = 'new-alias'

    async def handle(request):
        nonlocal created
        calls.append(request)
        if request.method == 'GET':
            return httpx.Response(
                200,
                json={
                    'keys': [existing_key(key_org, key_alias=alias)] if created else [],
                    'total_count': int(created),
                },
            )
        assert request.url.path == '/key/generate'
        created = True
        raise httpx.ReadTimeout('Response lost after creation', request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(httpx.ReadTimeout):
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), alias, None
            )
        with pytest.raises(BudgetWriteDenied, match='explicit credential recovery'):
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), alias, None
            )
    assert [call.url.path for call in calls if call.method == 'POST'] == [
        '/key/generate'
    ]


@pytest.mark.asyncio
async def test_owned_blocked_key_is_reused_without_an_auth_probe_or_mutation(key_org):
    client, calls = client_for([existing_key(key_org, max_budget=0, blocked=True)])

    async def verify(*args, **kwargs):
        return await LiteLlmManager._verify_existing_key(client, *args, **kwargs)

    async with client:
        with (
            patch.object(
                LiteLlmManager,
                'verify_existing_key',
                new=verify,
            ),
            patch.object(LiteLlmManager, 'generate_key', AsyncMock()) as generate,
            patch.object(LiteLlmManager, 'verify_key', AsyncMock()) as auth_probe,
        ):
            result = await LiteLlmManager.ensure_managed_key(
                'user-1', str(key_org), 'sk-existing', openhands_type=True
            )
    assert result == 'sk-existing'
    generate.assert_not_awaited()
    auth_probe.assert_not_awaited()
    assert len(calls) == 1 and calls[0].method == 'GET'


@pytest.mark.asyncio
async def test_missing_key_with_its_existing_alias_is_not_silently_replaced(key_org):
    alias = get_openhands_cloud_key_alias('user-1', str(key_org))
    client, calls = client_for([existing_key(key_org, key_alias=alias)])
    async with client:
        with pytest.raises(BudgetWriteDenied, match='Existing key policy'):
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), alias, None
            )
    assert all(call.method == 'GET' for call in calls)
