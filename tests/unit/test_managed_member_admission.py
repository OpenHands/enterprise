"""Provisioning must apply a durable member allowance before exposing a key."""

import asyncio
import hashlib
import json
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from openhands.app_server.settings.settings_models import Settings
from server.maintenance_task_processor.managed_llm_key_ownership_processor import (
    ManagedLlmKeyOwnershipProcessor,
)
from storage.budget_control import (
    BudgetControlConflict,
    BudgetWriteDenied,
    budget_control_session,
)
from storage.lite_llm_manager import LiteLlmManager
from storage.llm_credential_operation import LlmCredentialOperation
from storage.org import Org
from storage.org_budget_operation import OrgBudgetOperation
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_member import OrgMember
from storage.org_member_store import OrgMemberStore
from storage.role import Role
from storage.saas_settings_store import SaasSettingsStore
from tests.unit.test_budget_adoption_service import adoption as adoption_fixture

adoption = adoption_fixture


@pytest.fixture
async def admission(adoption, create_user, async_session_maker):
    org_id, original_user, service, request, proxy = adoption
    assert (await service.confirm(org_id, 'admin', request))['status'] == 'applied'
    new_user = create_user(current_org_id=org_id)
    user_id = str(new_user.id)
    async with async_session_maker() as session:
        org = await session.get(Org, org_id)
        org.agent_settings = {'llm': {'model': 'openhands/test-model'}}
        role_id = await session.scalar(select(Role.id))
        await session.commit()

    class Native:
        calls = []
        keys = []
        failure = None
        user_present = True
        before_key = None

        @classmethod
        async def handle(cls, incoming):
            endpoint = incoming.url.path
            body = json.loads(incoming.content) if incoming.content else None
            cls.calls.append((endpoint, body))
            if endpoint == '/team/info':
                return httpx.Response(
                    200,
                    json={
                        'team_info': {
                            'team_id': str(org_id),
                            'members_with_roles': [
                                {'user_id': u, 'role': 'user'}
                                for u in proxy.state['members']
                            ],
                        },
                        'team_memberships': [
                            {'user_id': u} for u in proxy.state['member_counters']
                        ],
                    },
                )
            if endpoint == '/user/info':
                if not cls.user_present:
                    return httpx.Response(404)
                return httpx.Response(
                    200, json={'user_info': {'user_id': user_id}, 'keys': cls.keys}
                )
            if endpoint == '/key/list':
                return httpx.Response(200, json={'keys': [], 'total_count': 0})
            if endpoint == '/key/info':
                key = next(
                    (
                        key
                        for key in cls.keys
                        if key['token'] == incoming.url.params['key']
                    ),
                    None,
                )
                return (
                    httpx.Response(200, json={'info': key})
                    if key
                    else httpx.Response(404)
                )
            if endpoint == '/key/delete':
                if cls.failure == 'before_revoke':
                    cls.failure = None
                    raise httpx.ReadTimeout('before key revocation')
                cls.keys[:] = [
                    key for key in cls.keys if key['token'] not in body['keys']
                ]
                if cls.failure == 'after_revoke':
                    cls.failure = None
                    raise httpx.ReadTimeout('after key revocation')
                return httpx.Response(200, json={})
            if endpoint == '/team/member_add':
                assert body == {
                    'team_id': str(org_id),
                    'member': {'user_id': user_id, 'role': 'user'},
                    'max_budget_in_team': 0,
                }
                async with async_session_maker() as session:
                    member = await session.get(
                        OrgMember, {'org_id': org_id, 'user_id': new_user.id}
                    )
                    assert member is not None
                    assert member.managed_llm_key_ownership_version == 0
                    assert member.llm_api_key.get_secret_value() == ''
                if cls.failure == 'before_member':
                    cls.failure = None
                    raise httpx.ReadTimeout('before member effect')
                assert user_id not in proxy.state['member_counters']
                proxy.state['member_counters'][user_id] = {
                    **deepcopy(proxy.state['member_counters'][original_user]),
                    'spend': 0,
                    'budget_id': 'new-private',
                    'effective_budget_id': 'new-private',
                }
                proxy.state['members'][user_id] = {
                    'spend': 0,
                    'max_budget': 0,
                    'uses_shared_budget': False,
                }
                proxy.state['control_policy']['members'][user_id] = {'max_budget': 0}
                if cls.failure == 'after_member':
                    cls.failure = None
                    raise httpx.ReadTimeout('after member effect')
                return httpx.Response(200, json={})
            assert endpoint == '/key/generate'
            if cls.before_key is not None:
                await cls.before_key()
            assert proxy.state['members'][user_id]['max_budget'] == 10
            assert proxy.state['members'][original_user]['max_budget'] == 22
            assert proxy.state['team_max_budget'] == 140
            async with async_session_maker() as session:
                operations = (await session.scalars(select(OrgBudgetOperation))).all()
                assert len(operations) >= 2
                assert all(op.status == 'applied' for op in operations)
                candidate = await session.scalar(
                    select(LlmCredentialOperation).where(
                        LlmCredentialOperation.key_hash
                        == hashlib.sha256(body['key'].encode()).hexdigest()
                    )
                )
                assert candidate.status == 'pending'
                assert candidate.payload['key'] == body['key']
            cls.keys.append(
                {
                    'token': hashlib.sha256(body['key'].encode()).hexdigest(),
                    'user_id': user_id,
                    'team_id': str(org_id),
                    'key_alias': body['key_alias'],
                }
            )
            if cls.failure == 'after_key':
                cls.failure = None
                raise httpx.ReadTimeout('after key effect')
            return httpx.Response(200, json={'key': body['key']})

    real_client = httpx.AsyncClient
    with (
        patch('storage.database.a_session_maker', async_session_maker),
        patch('storage.org_member_store.a_session_maker', async_session_maker),
        patch('storage.user_store.a_session_maker', async_session_maker),
        patch('storage.org_store.a_session_maker', async_session_maker),
        patch('storage.saas_settings_store.a_session_maker', async_session_maker),
        patch(
            'server.maintenance_task_processor.managed_llm_key_ownership_processor.a_session_maker',
            async_session_maker,
        ),
        patch('storage.lite_llm_manager.LITE_LLM_API_KEY', 'test-master'),
        patch('storage.lite_llm_manager.LITE_LLM_API_URL', 'http://litellm.test'),
        patch(
            'storage.lite_llm_manager.httpx.AsyncClient',
            side_effect=lambda **kwargs: real_client(
                transport=httpx.MockTransport(Native.handle), **kwargs
            ),
        ),
    ):
        yield org_id, original_user, new_user.id, role_id, proxy, Native


@pytest.mark.asyncio
async def test_provisioning_defers_native_writes_until_membership_commits(admission):
    org_id, _, user_id, _, _, native = admission
    with (
        patch.dict('os.environ', {'LOCAL_DEPLOYMENT': ''}),
        patch(
            'storage.lite_llm_manager.should_use_direct_llm_defaults',
            return_value=False,
        ),
    ):
        settings = await LiteLlmManager.create_entries(
            str(org_id), str(user_id), Settings(), create_user=False
        )
    assert settings.agent_settings.llm.api_key is None
    assert native.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'failure', [None, 'before_member', 'after_member', 'after_budget', 'after_key']
)
async def test_member_admission_and_retry_never_copy_team_cap_or_grant_twice(
    admission, async_session_maker, failure
):
    org_id, original_id, user_id, role_id, proxy, native = admission
    native.failure = failure
    if failure == 'after_budget':
        proxy.lose_next_response = True
    member = await OrgMemberStore.add_user_to_org(org_id, user_id, role_id, '')
    if failure:
        assert member.managed_llm_key_ownership_version == 0
        assert member.llm_api_key.get_secret_value() == ''
        assert (
            await ManagedLlmKeyOwnershipProcessor.repair_member(org_id, user_id)
            == 'repaired'
        )
    else:
        assert member.managed_llm_key_ownership_version == 1
    assert (
        await ManagedLlmKeyOwnershipProcessor.repair_member(org_id, user_id)
        == 'skipped'
    )
    async with async_session_maker() as session:
        settings = await session.scalar(select(OrgBudgetSettings))
        member = await session.get(OrgMember, {'org_id': org_id, 'user_id': user_id})
        credential = await session.scalar(select(LlmCredentialOperation))
        assert member.llm_api_key.get_secret_value() == credential.payload['key']
        assert credential.status == 'issued'
        assert settings.user_cycle_start_spend == {original_id: 12, str(user_id): 0}
        assert settings.control_generation == 2
    assert len([path for path, _ in native.calls if path == '/key/generate']) == 1
    assert proxy.state['members'][str(user_id)]['max_budget'] == 10


@pytest.mark.asyncio
async def test_member_key_save_failure_recovers_the_same_candidate(
    admission, async_session_maker
):
    org_id, _, user_id, role_id, _, native = admission
    failed = False

    def fail_key_save(session):
        nonlocal failed
        if not failed and any(
            isinstance(row, OrgMember)
            and row.user_id == user_id
            and row.managed_llm_key_ownership_version == 1
            for row in session.dirty
        ):
            failed = True
            raise RuntimeError('member key commit failed')

    event.listen(Session, 'before_commit', fail_key_save)
    try:
        member = await OrgMemberStore.add_user_to_org(org_id, user_id, role_id, '')
    finally:
        event.remove(Session, 'before_commit', fail_key_save)
    assert failed
    assert member.managed_llm_key_ownership_version == 0
    assert member.llm_api_key.get_secret_value() == ''
    assert (
        await ManagedLlmKeyOwnershipProcessor.repair_member(org_id, user_id)
        == 'repaired'
    )
    assert len(native.keys) == 1
    assert len([path for path, _ in native.calls if path == '/key/generate']) == 1


@pytest.mark.asyncio
async def test_missing_native_user_is_not_recreated_by_admission(admission):
    org_id, _, user_id, role_id, proxy, native = admission
    native.user_present = False
    member = await OrgMemberStore.add_user_to_org(org_id, user_id, role_id, '')
    assert member.managed_llm_key_ownership_version == 0
    assert str(user_id) not in proxy.state['member_counters']
    assert all(body is None for _, body in native.calls)


@pytest.mark.asyncio
async def test_settings_load_recovers_pending_admission_without_waiting_for_cron(
    admission,
):
    org_id, _, user_id, role_id, _, native = admission
    native.failure = 'after_key'
    member = await OrgMemberStore.add_user_to_org(org_id, user_id, role_id, '')
    assert member.managed_llm_key_ownership_version == 0
    with patch(
        'storage.saas_settings_store.get_openhands_default_model_name',
        AsyncMock(return_value=None),
    ):
        settings = await SaasSettingsStore(str(user_id), effective_org_id=org_id).load()
    assert settings is not None
    assert (
        hashlib.sha256(
            settings.agent_settings.llm.api_key.get_secret_value().encode()
        ).hexdigest()
        == native.keys[0]['token']
    )
    assert len([path for path, _ in native.calls if path == '/key/generate']) == 1


@pytest.mark.asyncio
async def test_rotation_in_adopted_org_recovers_without_changing_allowances(admission):
    org_id, _, user_id, role_id, proxy, native = admission
    member = await OrgMemberStore.add_user_to_org(org_id, user_id, role_id, '')
    original = member.llm_api_key.get_secret_value()
    before = deepcopy(proxy.state)
    native.failure = 'after_key'
    with pytest.raises(httpx.ReadTimeout, match='after key effect'):
        await LiteLlmManager.generate_key(
            str(user_id), str(org_id), 'rotate', None, replacing_key=original
        )
    replacement = await LiteLlmManager.generate_key(
        str(user_id), str(org_id), 'rotate', None, replacing_key=original
    )
    assert replacement != original
    assert proxy.state == before
    assert len(native.keys) == 2
    assert len([path for path, _ in native.calls if path == '/key/generate']) == 2


@pytest.mark.asyncio
async def test_key_cannot_be_issued_before_app_membership(admission):
    org_id, _, user_id, _, _, native = admission
    with pytest.raises(BudgetWriteDenied, match='Commit organization membership'):
        await LiteLlmManager.generate_key(str(user_id), str(org_id), 'alias', None)
    assert native.calls == []


@pytest.mark.asyncio
async def test_initializer_cannot_reset_an_already_budgeted_member(
    admission, async_engine
):
    org_id, original_id, _, _, _, native = admission
    async with budget_control_session(async_engine, org_id):
        with pytest.raises(BudgetWriteDenied, match='admission authority'):
            await LiteLlmManager.prepare_new_managed_member(original_id, str(org_id))
    assert native.calls == []


@pytest.mark.asyncio
async def test_admission_holds_org_lock_through_key_issuance_and_member_save(
    admission, async_engine
):
    org_id, _, user_id, role_id, _, native = admission
    reached, release = asyncio.Event(), asyncio.Event()

    async def pause():
        reached.set()
        await release.wait()

    native.before_key = pause
    admission_task = asyncio.create_task(
        OrgMemberStore.add_user_to_org(org_id, user_id, role_id, '')
    )
    try:
        await asyncio.wait_for(reached.wait(), timeout=15)
        with pytest.raises(BudgetControlConflict):
            await OrgMemberStore.remove_user_from_org(org_id, user_id)
        with pytest.raises(BudgetControlConflict):
            async with budget_control_session(async_engine, org_id):
                pytest.fail('Concurrent budget writer acquired the admission lock')
    finally:
        release.set()
        member = await admission_task
    assert member.managed_llm_key_ownership_version == 1
    assert member.llm_api_key.get_secret_value()
