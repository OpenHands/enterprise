"""Credential creation uses the budget lock and preserves independent key policy."""

import asyncio
import contextvars
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from storage.budget_control import (
    BudgetControlConflict,
    BudgetWriteDenied,
    budget_control_session,
    current_budget_control,
)
from storage.lite_llm_manager import LiteLlmManager, get_openhands_cloud_key_alias
from storage.litellm_credentials import activate_credential, retire_replaced_credentials
from storage.litellm_key_policy import key_mutation_scope, key_restrictions
from storage.llm_credential_operation import LlmCredentialOperation
from storage.org import Org
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_member import OrgMember
from storage.role import Role
from storage.saas_settings_store import SaasSettingsStore

GENERATED_KEY = 'sk-' + 'a' * 64


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
        patch('storage.litellm_credentials.secrets.token_hex', return_value='a' * 64),
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
            return httpx.Response(
                user_status,
                json={
                    'keys': keys,
                    'user_info': {'user_id': request.url.params['user_id']},
                },
            )
        if request.url.path == '/key/list':
            assert request.url.params['size'] == '1'
            return httpx.Response(
                200, json=alias_result or {'keys': [], 'total_count': 0}
            )
        if request.url.path == '/key/info':
            row = next(
                (key for key in keys if key['token'] == request.url.params['key']), None
            )
            return (
                httpx.Response(200, json={'info': row}) if row else httpx.Response(404)
            )
        if request.url.path == '/key/delete':
            targets = json.loads(request.content)['keys']
            hashes = {
                hashlib.sha256(key.encode()).hexdigest()
                if key.startswith('sk-')
                else key
                for key in targets
            }
            keys[:] = [key for key in keys if key['token'] not in hashes]
            return httpx.Response(200, json={'deleted_keys': targets})
        assert request.url.path == '/key/generate'
        payload = json.loads(request.content)
        keys.append(
            {
                'token': hashlib.sha256(payload['key'].encode()).hexdigest(),
                'user_id': payload['user_id'],
                'team_id': payload['team_id'],
                'key_alias': payload.get('key_alias'),
            }
        )
        return httpx.Response(200, json={'key': payload['key']})

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


@pytest.mark.parametrize('mode', ['external', 'needs_adoption'])
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
            == GENERATED_KEY
        )
    assert [call.url.path for call in calls] == [
        '/user/info',
        '/key/list',
        '/key/generate',
        '/user/info',
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
@pytest.mark.parametrize(
    'user_status,user_info',
    [
        (404, None),
        (200, None),
        (200, {}),
        (200, {'user_id': 'different'}),
    ],
)
async def test_issuance_cannot_recreate_missing_or_unverified_global_user(
    key_org, user_status, user_info
):
    calls = []

    async def handle(request):
        calls.append(request)
        return httpx.Response(user_status, json={'keys': [], 'user_info': user_info})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(BudgetWriteDenied, match='unavailable'):
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), 'alias', None
            )
    assert len(calls) == 1 and calls[0].method == 'GET'


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
            == GENERATED_KEY
        )
    assert len([call for call in calls if call.method == 'POST']) == 1


@pytest.mark.asyncio
async def test_cancellation_during_key_creation_releases_org_lock(
    key_org, async_engine
):
    reached_write = asyncio.Event()

    async def handle(request):
        if request.method == 'GET':
            return httpx.Response(
                200,
                json={
                    'keys': [],
                    'total_count': 0,
                    'user_info': {'user_id': request.url.params.get('user_id')},
                },
            )
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
        with pytest.raises(BudgetWriteDenied, match='database is not bound'):
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
                    'keys': [
                        existing_key(
                            key_org,
                            key_alias=alias,
                            token=hashlib.sha256(GENERATED_KEY.encode()).hexdigest(),
                        )
                    ]
                    if created
                    else [],
                    'total_count': int(created),
                    'user_info': {'user_id': request.url.params.get('user_id')},
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
        assert (
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), alias, None
            )
            == GENERATED_KEY
        )
    assert [call.url.path for call in calls if call.method == 'POST'] == [
        '/key/generate'
    ]


@pytest.mark.asyncio
async def test_candidate_is_durable_and_encrypted_before_remote_effect(
    key_org, async_session_maker
):
    keys = []

    async def handle(request):
        if request.method == 'GET':
            return httpx.Response(
                200,
                json={
                    'keys': keys,
                    'total_count': 0,
                    'user_info': {'user_id': request.url.params.get('user_id')},
                },
            )
        payload = json.loads(request.content)
        async with async_session_maker() as session:
            operation = await session.scalar(select(LlmCredentialOperation))
            assert operation.status == 'pending'
            assert operation.payload == payload
            raw = await session.scalar(
                text('SELECT payload FROM llm_credential_operation')
            )
            assert payload['key'] not in raw
            assert payload['user_id'] not in raw
        keys.append(
            existing_key(
                key_org, token=hashlib.sha256(payload['key'].encode()).hexdigest()
            )
        )
        return httpx.Response(200, json={'key': payload['key']})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        assert (
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), 'new-alias', None
            )
            == GENERATED_KEY
        )
    async with async_session_maker() as session:
        assert (await session.scalar(select(LlmCredentialOperation))).status == 'issued'


@pytest.mark.asyncio
async def test_bootstrap_recovery_does_not_require_committed_account_rows(
    key_org, async_session_maker
):
    org_id = uuid4()
    client, calls = client_for([])
    async with client:
        first = await LiteLlmManager._generate_key(
            client, 'new-user', str(org_id), 'bootstrap', None
        )
        assert (
            await LiteLlmManager._generate_key(
                client, 'new-user', str(org_id), 'bootstrap', None
            )
            == first
        )
    async with async_session_maker() as session:
        assert await session.get(Org, org_id) is None
        assert (await session.scalar(select(LlmCredentialOperation))).org_id == org_id
    assert sum(call.method == 'POST' for call in calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['before_effect', 'verification'])
async def test_issuance_retry_uses_original_candidate_after_failure(key_org, failure):
    keys, candidates = [], []
    fail = True

    async def handle(request):
        nonlocal fail
        if request.method == 'GET':
            if failure == 'verification' and candidates and fail:
                fail = False
                return httpx.Response(503)
            return httpx.Response(
                200,
                json={
                    'keys': keys,
                    'total_count': 0,
                    'user_info': {'user_id': request.url.params.get('user_id')},
                },
            )
        payload = json.loads(request.content)
        candidates.append(payload['key'])
        if failure == 'before_effect' and fail:
            fail = False
            return httpx.Response(503)
        keys.append(
            existing_key(
                key_org, token=hashlib.sha256(payload['key'].encode()).hexdigest()
            )
        )
        return httpx.Response(200, json={'key': payload['key']})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises((httpx.HTTPStatusError, BudgetWriteDenied)):
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), 'alias', None
            )
        assert (
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), 'alias', None
            )
            == GENERATED_KEY
        )
    assert set(candidates) == {GENERATED_KEY}
    assert len(candidates) == (2 if failure == 'before_effect' else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('revoked', [True, False])
async def test_issued_key_is_not_resurrected_after_deletion_or_revocation(
    key_org, async_session_maker, revoked
):
    keys = []
    client, calls = client_for(keys)
    async with client:
        await LiteLlmManager._generate_key(
            client, 'user-1', str(key_org), 'alias', None
        )
        if revoked:
            async with async_session_maker() as session:
                operation = await session.scalar(select(LlmCredentialOperation))
                operation.status = 'revoked'
                await session.commit()
        else:
            keys.clear()
        with pytest.raises(BudgetWriteDenied, match='revoked|missing'):
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), 'alias', None
            )
    assert sum(call.method == 'POST' for call in calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'mutation', ['payload', 'request_hash', 'key_hash', 'org_id', 'status']
)
async def test_issued_credential_intent_cannot_be_rewritten(
    key_org, async_session_maker, mutation
):
    client, _ = client_for([])
    async with client:
        await LiteLlmManager._generate_key(
            client, 'user-1', str(key_org), 'alias', None
        )
    async with async_session_maker() as session:
        operation = await session.scalar(select(LlmCredentialOperation))
        values = {
            'payload': {'key': 'different'},
            'request_hash': 'different',
            'key_hash': 'different',
            'org_id': uuid4(),
            'status': 'pending',
        }
        setattr(operation, mutation, values[mutation])
        with pytest.raises(DBAPIError, match='immutable|cannot be reopened'):
            await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['external', 'needs_adoption'])
async def test_unrestricted_owned_rotation_is_recoverable_without_changing_budgets(
    key_org, async_session_maker, mode
):
    async with async_session_maker() as session:
        settings = await session.scalar(select(OrgBudgetSettings))
        settings.control_mode = mode
        await session.commit()
    keys = [existing_key(key_org)]
    original = dict(keys[0])
    client, calls = client_for(keys)
    async with client:
        for _ in range(2):
            assert (
                await LiteLlmManager._generate_key(
                    client,
                    'user-1',
                    str(key_org),
                    'alias',
                    None,
                    replacing_key='sk-existing',
                )
                == GENERATED_KEY
            )
    assert keys[0] == original
    writes = [call for call in calls if call.method == 'POST']
    assert len(writes) == 1
    assert writes[0].url.path == '/key/generate'
    assert json.loads(writes[0].content)['key_alias'].startswith('openhands-rotation-')


@pytest.mark.asyncio
@pytest.mark.parametrize('replacing_key', [None, 'sk-existing'])
async def test_unadopted_managed_mode_cannot_issue_credentials(
    key_org, async_session_maker, replacing_key
):
    async with async_session_maker() as session:
        settings = await session.scalar(select(OrgBudgetSettings))
        settings.control_mode = 'managed'
        await session.commit()
    client, calls = client_for([existing_key(key_org)])
    async with client:
        with pytest.raises(BudgetWriteDenied, match='verified budget adoption'):
            await LiteLlmManager._generate_key(
                client,
                'user-1',
                str(key_org),
                'alias',
                None,
                replacing_key=replacing_key,
            )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'restriction',
    [
        {'blocked': True},
        {'max_budget': 0},
        {'rpm_limit': 10},
        {'models': ['approved']},
        {'budget_reset_at': '2026-10-01'},
        {'user_id': 'different'},
        {'team_id': 'different'},
    ],
)
async def test_rotation_preserves_independent_policy_and_rejects_unowned_key(
    key_org, restriction
):
    client, calls = client_for([existing_key(key_org, **restriction)])
    async with client:
        with pytest.raises(BudgetWriteDenied, match='Rotation requires'):
            await LiteLlmManager._generate_key(
                client,
                'user-1',
                str(key_org),
                'alias',
                None,
                replacing_key='sk-existing',
            )
    assert all(call.method == 'GET' for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'lost_response', 'before_effect'])
async def test_revocation_records_tombstone_before_effect_and_never_recreates(
    key_org, async_session_maker, failure
):
    client, _ = client_for([])
    async with client:
        await LiteLlmManager._generate_key(
            client, 'user-1', str(key_org), 'alias', None
        )
    deleted = False
    fail = bool(failure)
    writes = []

    async def handle(request):
        nonlocal deleted, fail
        if request.url.path == '/key/info':
            assert (
                request.url.params['key']
                == hashlib.sha256(GENERATED_KEY.encode()).hexdigest()
            )
            return (
                httpx.Response(404)
                if deleted
                else httpx.Response(
                    200, json={'info': {'user_id': 'user-1', 'team_id': str(key_org)}}
                )
            )
        assert request.url.path == '/key/delete'
        writes.append(json.loads(request.content))
        async with async_session_maker() as session:
            operation = await session.scalar(select(LlmCredentialOperation))
            assert operation.status == 'revoked'
        if fail and failure == 'before_effect':
            fail = False
            return httpx.Response(503)
        deleted = True
        if fail:
            fail = False
            raise httpx.ReadTimeout('Lost deletion response', request=request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        if failure:
            with pytest.raises((httpx.HTTPStatusError, httpx.ReadTimeout)):
                await LiteLlmManager._delete_key(client, GENERATED_KEY)
        await LiteLlmManager._delete_key(client, GENERATED_KEY)
        with pytest.raises(BudgetWriteDenied, match='revoked'):
            await LiteLlmManager._generate_key(
                client, 'user-1', str(key_org), 'alias', None
            )
    assert deleted
    assert all(write == {'keys': [GENERATED_KEY]} for write in writes)


@pytest.mark.asyncio
async def test_revocation_does_not_delete_credential_reassigned_to_other_org(key_org):
    keys = []
    client, calls = client_for(keys)
    async with client:
        await LiteLlmManager._generate_key(
            client, 'user-1', str(key_org), 'alias', None
        )
        keys[0]['team_id'] = str(uuid4())
        with pytest.raises(BudgetWriteDenied, match='ownership changed'):
            await LiteLlmManager._delete_key(client, GENERATED_KEY)
    assert [call.url.path for call in calls if call.method == 'POST'] == [
        '/key/generate'
    ]


@pytest.mark.asyncio
async def test_exact_revocation_is_allowed_during_pending_adoption(
    key_org, async_engine
):
    keys = []
    client, calls = client_for(keys)
    async with client:
        await LiteLlmManager._generate_key(
            client, 'user-1', str(key_org), 'alias', None
        )
        async with budget_control_session(async_engine, key_org) as control:
            await control.reserve_operation(
                idempotency_key='adopt',
                request_hash='test',
                kind='adopt',
                actor='admin',
                plan={},
            )
        await LiteLlmManager._delete_key(client, GENERATED_KEY)
    assert keys == []
    assert [call.url.path for call in calls if call.method == 'POST'] == [
        '/key/generate',
        '/key/delete',
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


@pytest.fixture
async def retirement_member(key_org, create_user, async_session_maker):
    user_id = create_user(current_org_id=key_org).id
    async with async_session_maker() as session:
        role = Role(name='owner', rank=1)
        session.add(role)
        await session.flush()
        session.add(
            OrgMember(
                org_id=key_org,
                user_id=user_id,
                role_id=role.id,
                llm_api_key='sk-existing',
                has_custom_llm_api_key=False,
            )
        )
        await session.commit()
    return user_id


@pytest.mark.asyncio
async def test_retirement_requires_atomic_member_commit_and_activation_receipt(
    key_org, retirement_member, async_session_maker
):
    user_id = str(retirement_member)
    keys = [existing_key(key_org, user_id=user_id)]
    client, calls = client_for(keys)
    async with client:
        candidate = await LiteLlmManager._generate_key(
            client, user_id, str(key_org), 'alias', None, replacing_key='sk-existing'
        )
        for commit in (False, True):
            async with key_mutation_scope(str(key_org)):
                async with async_session_maker() as session:
                    member = await session.get(OrgMember, (key_org, retirement_member))
                    member.llm_api_key = candidate
                    await activate_credential(session, key_org, user_id, candidate)
                    await session.flush()
                    async with async_session_maker() as observer:
                        observed = await observer.get(
                            OrgMember, (key_org, retirement_member)
                        )
                        assert observed.llm_api_key.get_secret_value() == 'sk-existing'
                        operation = await observer.scalar(
                            select(LlmCredentialOperation)
                        )
                        assert operation.activated_at is None
                    if commit:
                        await session.commit()
                    else:
                        await session.rollback()
            with patch.object(
                LiteLlmManager,
                'delete_key',
                new=lambda key: LiteLlmManager._delete_key(client, key),
            ):
                result = await retire_replaced_credentials(key_org)
            assert result == {'retired': int(commit), 'error_count': 0}
            assert len(keys) == (1 if commit else 2)
    async with async_session_maker() as session:
        operation = await session.scalar(select(LlmCredentialOperation))
        assert operation.activated_at is not None
        assert operation.retired_at is not None
        assert operation.payload['_retire_key'] == 'sk-existing'
    generation = next(call for call in calls if call.url.path == '/key/generate')
    assert '_retire_key' not in json.loads(generation.content)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['before_effect', 'lost_response', 'false_ack'])
async def test_retirement_recovers_failures_without_rotating_again(
    key_org, retirement_member, async_session_maker, failure
):
    user_id = str(retirement_member)
    keys = [existing_key(key_org, user_id=user_id)]
    client, _ = client_for(keys)
    async with client:
        candidate = await LiteLlmManager._generate_key(
            client, user_id, str(key_org), 'alias', None, replacing_key='sk-existing'
        )
    async with key_mutation_scope(str(key_org)):
        async with async_session_maker() as session:
            member = await session.get(OrgMember, (key_org, retirement_member))
            member.llm_api_key = candidate
            await activate_credential(session, key_org, user_id, candidate)
            await session.commit()
    fail = True
    deleted = False
    writes = []

    async def handle(request):
        nonlocal fail, deleted
        if request.url.path == '/key/info':
            return (
                httpx.Response(404)
                if deleted
                else httpx.Response(200, json={'info': keys[0]})
            )
        assert request.url.path == '/key/delete'
        writes.append(json.loads(request.content))
        if fail:
            fail = False
            if failure == 'before_effect':
                return httpx.Response(503)
            if failure == 'false_ack':
                return httpx.Response(200, json={})
            deleted = True
            raise httpx.ReadTimeout('Lost cleanup response', request=request)
        deleted = True
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with patch.object(
            LiteLlmManager,
            'delete_key',
            new=lambda key: LiteLlmManager._delete_key(client, key),
        ):
            assert await retire_replaced_credentials(key_org) == {
                'retired': 0,
                'error_count': 1,
            }
            async with async_session_maker() as session:
                operation = await session.scalar(select(LlmCredentialOperation))
                assert operation.retired_at is None
            assert await retire_replaced_credentials(key_org) == {
                'retired': 1,
                'error_count': 0,
            }
            assert await retire_replaced_credentials(key_org) == {
                'retired': 0,
                'error_count': 0,
            }
    assert deleted
    assert all(write == {'keys': ['sk-existing']} for write in writes)


@pytest.mark.asyncio
async def test_activation_receipt_cannot_be_committed_for_pending_key(
    key_org, async_session_maker
):
    async with async_session_maker() as session:
        session.add(
            LlmCredentialOperation(
                org_id=key_org,
                user_id='user-1',
                key_hash=hashlib.sha256(GENERATED_KEY.encode()).hexdigest(),
                request_hash='pending',
                payload={'key': GENERATED_KEY},
                status='pending',
            )
        )
        await session.commit()
    async with key_mutation_scope(str(key_org)):
        async with async_session_maker() as session:
            with pytest.raises(BudgetWriteDenied, match='verified issuance'):
                await activate_credential(session, key_org, 'user-1', GENERATED_KEY)


@pytest.mark.asyncio
async def test_real_rotation_store_recovers_failed_member_commit(
    key_org, retirement_member, async_session_maker
):
    user_id = str(retirement_member)
    keys = [existing_key(key_org, user_id=user_id)]
    client, calls = client_for(keys)
    store = SaasSettingsStore(user_id, effective_org_id=key_org)
    settings = MagicMock()
    settings.agent_settings.llm.model = 'openhands/claude-sonnet-4'
    settings.agent_settings.llm.base_url = None
    fail_commit = True
    original_commit = AsyncSession.commit

    async def commit(session):
        nonlocal fail_commit
        if fail_commit and any(
            isinstance(row, OrgMember)
            and row.llm_api_key.get_secret_value() == GENERATED_KEY
            for row in session.identity_map.values()
        ):
            fail_commit = False
            raise RuntimeError('Simulated member commit failure')
        await original_commit(session)

    async def generate(*args, **kwargs):
        return await LiteLlmManager._generate_key(client, *args, **kwargs)

    async with client:
        with (
            patch('storage.saas_settings_store.a_session_maker', async_session_maker),
            patch.object(store, 'load', return_value=settings),
            patch.object(LiteLlmManager, 'generate_key', new=generate),
            patch.object(AsyncSession, 'commit', new=commit),
        ):
            with pytest.raises(RuntimeError, match='member commit failure'):
                await store.rotate_managed_llm_key()
            async with async_session_maker() as session:
                member = await session.get(OrgMember, (key_org, retirement_member))
                assert member.llm_api_key.get_secret_value() == 'sk-existing'
                operation = await session.scalar(select(LlmCredentialOperation))
                assert operation.status == 'issued'
                assert operation.activated_at is None
            rotation = await store.rotate_managed_llm_key()
            assert rotation.new_key == GENERATED_KEY
            assert rotation.old_key == 'sk-existing'
        with patch.object(
            LiteLlmManager,
            'delete_key',
            new=lambda key: LiteLlmManager._delete_key(client, key),
        ):
            assert await retire_replaced_credentials(key_org) == {
                'retired': 1,
                'error_count': 0,
            }
    assert [call.url.path for call in calls if call.method == 'POST'] == [
        '/key/generate',
        '/key/delete',
    ]
    async with async_session_maker() as session:
        member = await session.get(OrgMember, (key_org, retirement_member))
        assert member.llm_api_key.get_secret_value() == GENERATED_KEY
        operation = await session.scalar(select(LlmCredentialOperation))
        assert operation.activated_at is not None and operation.retired_at is not None
