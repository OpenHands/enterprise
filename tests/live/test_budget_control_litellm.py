"""Real local LiteLLM/PostgreSQL integration; the upstream response is synthetic."""

import asyncio
import os
from contextlib import closing
from datetime import timedelta
from urllib.parse import urlsplit

import docker
import httpx
import pytest
from sqlalchemy import select

from server.services.budget_adoption_plan import BudgetAdoptionRequest
from server.services.budget_adoption_service import BudgetAdoptionService
from server.services.managed_budget_service import (
    ManagedBudgetService,
    ManagedBudgetUpdate,
)
from storage.lite_llm_manager import LiteLlmManager
from storage.litellm_credentials import ensure_byor_credential
from storage.org_budget_operation import OrgBudgetOperation
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_member import OrgMember
from storage.org_member_store import OrgMemberStore
from storage.role import Role


@pytest.fixture
async def live_proxy(monkeypatch, async_session_maker):
    url = os.environ.get('BUDGET_TEST_LITELLM_URL', '')
    if urlsplit(url).hostname not in {'127.0.0.1', 'localhost'}:
        raise pytest.UsageError(
            'BUDGET_TEST_LITELLM_URL must point to the disposable local proxy'
        )
    master = 'sk-budget-control-local-only'
    monkeypatch.setattr('storage.database.a_session_maker', async_session_maker)
    monkeypatch.setattr('storage.org_member_store.a_session_maker', async_session_maker)
    monkeypatch.setattr('storage.lite_llm_manager.LITE_LLM_API_URL', url)
    monkeypatch.setattr('storage.lite_llm_manager.LITE_LLM_API_KEY', master)
    async with httpx.AsyncClient(
        base_url=url, headers={'Authorization': f'Bearer {master}'}, timeout=30
    ) as client:
        response = await client.get('/health/readiness')
        response.raise_for_status()
        yield client


@pytest.fixture
async def restart_proxy(live_proxy):
    """Restart only the dedicated local compose proxy, never an arbitrary endpoint."""
    with closing(docker.from_env()) as client:
        containers = client.containers.list(
            filters={
                'label': [
                    'com.docker.compose.project=budget-control-local',
                    'com.docker.compose.service=proxy',
                ]
            }
        )
        assert len(containers) == 1, 'Expected exactly one disposable local proxy'
        container = containers[0]
        binding = container.attrs['NetworkSettings']['Ports']['4000/tcp']
        assert binding == [
            {'HostIp': '127.0.0.1', 'HostPort': str(live_proxy.base_url.port)}
        ]

        async def restart():
            await asyncio.to_thread(container.restart, timeout=10)
            for _ in range(90):
                try:
                    response = await live_proxy.get('/health/readiness', timeout=2)
                    if response.status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.5)
            raise AssertionError('Disposable proxy failed readiness after restart')

        yield restart


@pytest.fixture
async def live_member(live_proxy, create_org, create_user, async_session_maker):
    org = create_org()
    user = create_user(current_org_id=org.id)
    user_id, org_id = str(user.id), str(org.id)
    created_user = created_team = False
    try:
        response = await live_proxy.get('/team/info', params={'team_id': org_id})
        assert response.status_code == 404
        response = await live_proxy.post(
            '/user/new',
            json={
                'user_id': user_id,
                'auto_create_key': False,
                'send_invite_email': False,
            },
        )
        response.raise_for_status()
        created_user = True
        response = await live_proxy.post(
            '/team/new',
            json={
                'team_id': org_id,
                'team_alias': f'budget-local-{org_id}',
                'max_budget': 100,
                'budget_duration': '1d',
                'models': ['budget-test'],
            },
        )
        response.raise_for_status()
        created_team = True
        response = await live_proxy.post(
            '/team/member_add',
            json={
                'team_id': org_id,
                'member': {'user_id': user_id, 'role': 'user'},
                'max_budget_in_team': 20,
            },
        )
        response.raise_for_status()
        response = await live_proxy.post(
            '/key/generate',
            json={
                'team_id': org_id,
                'user_id': user_id,
                'metadata': {'type': 'openhands'},
            },
        )
        response.raise_for_status()
        key = response.json()['key']
        async with async_session_maker() as session:
            role = Role(name='owner', rank=1)
            session.add(role)
            await session.flush()
            session.add(
                OrgMember(
                    org_id=org.id, user_id=user.id, role_id=role.id, llm_api_key=key
                )
            )
            session.add(
                OrgBudgetSettings(
                    org_id=org.id,
                    enabled=True,
                    control_mode='needs_adoption',
                    monthly_limit=1,
                    default_user_monthly_limit=1,
                )
            )
            await session.commit()
        yield org.id, user.id, key
    finally:
        if created_team:
            response = await live_proxy.post(
                '/team/delete', json={'team_ids': [org_id]}
            )
            response.raise_for_status()
        if created_user:
            response = await live_proxy.post(
                '/user/delete', json={'user_ids': [user_id]}
            )
            response.raise_for_status()


async def infer(proxy, key):
    return await proxy.post(
        '/chat/completions',
        headers={'Authorization': f'Bearer {key}'},
        json={
            'model': 'budget-test',
            'messages': [{'role': 'user', 'content': 'Count this local budget test.'}],
            'max_tokens': 8,
        },
    )


async def metered_snapshot(org_id, user_id):
    for _ in range(45):
        observed = await LiteLlmManager.get_team_members_financial_data(
            str(org_id), include_control_policy=True
        )
        if (
            observed['team_spend'] > 0
            and observed['member_counters'][str(user_id)]['spend'] > 0
        ):
            return observed
        await asyncio.sleep(1)
    raise AssertionError(
        'Nonzero team/member metering was not observed within 45 seconds'
    )


@pytest.mark.asyncio
async def test_real_proxy_adoption_renewal_byor_and_removal(
    live_proxy, live_member, async_engine, async_session_maker
):
    org_id, user_id, key = live_member
    response = await infer(live_proxy, key)
    response.raise_for_status()
    assert response.json()['usage']['total_tokens'] > 0
    before = await metered_snapshot(org_id, user_id)
    # Repeated provisioning must not upsert the existing member's cap or counter.
    await LiteLlmManager.add_user_to_team(str(user_id), str(org_id), 1000)
    after_provisioning = await LiteLlmManager.get_team_members_financial_data(
        str(org_id), include_control_policy=True
    )
    assert after_provisioning == before
    service = BudgetAdoptionService(async_engine)
    preview = await service.preview(org_id)
    request = BudgetAdoptionRequest(
        preview_fingerprint=preview['fingerprint'],
        idempotency_key='live-adoption',
        current_team_allowance=10,
        current_default_member_allowance=5,
        future_monthly_limit=20,
        future_default_member_limit=7,
        reset_day=1,
        replace_native_reset_schedules=True,
    )
    result = await service.confirm(org_id, 'local-test-admin', request)
    assert result['status'] == 'applied', result
    retry = await service.confirm(org_id, 'local-test-admin', request)
    assert retry['operation_id'] == result['operation_id']
    observed = await LiteLlmManager.get_team_members_financial_data(
        str(org_id), include_control_policy=True
    )
    assert observed['team_spend'] == before['team_spend']
    assert observed['team_max_budget'] == pytest.approx(before['team_spend'] + 10)
    assert (
        observed['member_counters'][str(user_id)]['spend']
        == before['member_counters'][str(user_id)]['spend']
    )
    assert observed['control_policy']['team']['models'] == ['budget-test']
    assert observed['team_budget_duration'] is None

    byor = await ensure_byor_credential(org_id, user_id)
    assert await ensure_byor_credential(org_id, user_id) == byor
    rotated = await ensure_byor_credential(org_id, user_id, rotate=True)
    assert rotated != byor
    assert (await infer(live_proxy, byor)).status_code in {401, 403}
    async with async_session_maker() as session:
        settings = await session.scalar(select(OrgBudgetSettings))
        now = settings.cycle_end_at + timedelta(seconds=1)
    managed = ManagedBudgetService(async_engine)
    renewed = await managed.maintain(org_id, now=now)
    assert renewed['status'] == 'applied', renewed
    assert renewed['cycle_rolled']
    assert (await managed.maintain(org_id, now=now))['status'] == 'healthy'

    before_removal = await LiteLlmManager.get_team_members_financial_data(
        str(org_id), include_control_policy=True
    )
    assert await OrgMemberStore.remove_user_from_org(org_id, user_id)
    assert (await managed.maintain(org_id, now=now))['status'] in {'applied', 'healthy'}
    after_removal = await LiteLlmManager.get_team_members_financial_data(
        str(org_id), include_control_policy=True
    )
    assert (
        after_removal['member_counters'][str(user_id)]
        == before_removal['member_counters'][str(user_id)]
    )
    assert after_removal['control_policy']['members'][str(user_id)]['max_budget'] == 0
    assert (await infer(live_proxy, key)).status_code in {401, 403}
    assert (await infer(live_proxy, rotated)).status_code in {401, 403}
    # Even a separately issued native key must not evade the retained zero cap.
    issued = await live_proxy.post(
        '/key/generate', json={'team_id': str(org_id), 'user_id': str(user_id)}
    )
    issued.raise_for_status()
    denied = await infer(live_proxy, issued.json()['key'])
    assert denied.status_code == 429, denied.text
    assert 'budget' in denied.text.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize('zero_scope', ['team', 'member'])
async def test_real_proxy_zero_allowance_denies_and_raise_restores_access(
    live_proxy, live_member, async_engine, zero_scope, restart_proxy
):
    org_id, user_id, key = live_member
    (await infer(live_proxy, key)).raise_for_status()
    before = await metered_snapshot(org_id, user_id)
    service = ManagedBudgetService(async_engine)
    preview = await service.preview(org_id)
    request = BudgetAdoptionRequest(
        preview_fingerprint=preview['fingerprint'],
        idempotency_key='live-zero',
        current_team_allowance=0 if zero_scope == 'team' else 10,
        current_default_member_allowance=0 if zero_scope == 'member' else 5,
        future_monthly_limit=20,
        future_default_member_limit=7,
        reset_day=1,
        replace_native_reset_schedules=True,
    )
    adopted = await service.confirm(org_id, 'local-test-admin', request)
    assert adopted['status'] == 'applied', adopted
    denied = await infer(live_proxy, key)
    assert denied.status_code == (401 if zero_scope == 'team' else 429), denied.text
    assert ('blocked' if zero_scope == 'team' else 'budget') in denied.text.lower()
    await restart_proxy()
    denied_after_restart = await infer(live_proxy, key)
    assert denied_after_restart.status_code == denied.status_code
    update = ManagedBudgetUpdate(
        idempotency_key='restore-allowance',
        expected_generation=adopted['generation'],
        enabled=True,
        current_cycle_team_allowance=10,
        current_cycle_default_member_allowance=5,
        future_monthly_limit=20,
        future_default_member_limit=7,
        reset_day=1,
    )
    restored = await service.update(org_id, 'local-test-admin', update)
    assert restored['status'] == 'applied', restored
    observed = await LiteLlmManager.get_team_members_financial_data(
        str(org_id), include_control_policy=True
    )
    assert observed['team_spend'] == before['team_spend']
    assert observed['team_max_budget'] == pytest.approx(before['team_spend'] + 10)
    assert (
        observed['member_counters'][str(user_id)]
        == before['member_counters'][str(user_id)]
    )
    (await infer(live_proxy, key)).raise_for_status()


@pytest.mark.asyncio
@pytest.mark.parametrize('lost_path', ['/team/update', '/team/member_update'])
async def test_real_proxy_lost_write_response_replays_original_allowance(
    live_proxy, live_member, async_engine, async_session_maker, monkeypatch, lost_path
):
    org_id, user_id, key = live_member
    (await infer(live_proxy, key)).raise_for_status()
    before = await metered_snapshot(org_id, user_id)
    service = BudgetAdoptionService(async_engine)
    preview = await service.preview(org_id)
    request = BudgetAdoptionRequest(
        preview_fingerprint=preview['fingerprint'],
        idempotency_key='live-response-loss',
        current_team_allowance=10,
        current_default_member_allowance=5,
        future_monthly_limit=20,
        future_default_member_limit=7,
        reset_day=1,
        replace_native_reset_schedules=True,
    )
    real_write = LiteLlmManager.apply_budget_write
    response_lost = False

    async def lose_one_response(org_id, operation_id, path, body):
        nonlocal response_lost
        await real_write(org_id, operation_id, path, body)
        if path == lost_path and not response_lost:
            response_lost = True
            raise httpx.ReadTimeout('Injected response loss after real native effect')

    monkeypatch.setattr(LiteLlmManager, 'apply_budget_write', lose_one_response)
    pending = await service.confirm(org_id, 'local-test-admin', request)
    assert response_lost
    assert pending['status'] == 'pending', pending
    async with async_session_maker() as session:
        operation = await session.scalar(select(OrgBudgetOperation))
        original_plan = operation.plan
        original_id = operation.id
    # Fresh controller instance simulates a different retrying worker/process.
    replayed = await BudgetAdoptionService(async_engine).retry(org_id)
    assert replayed['status'] == 'applied', replayed
    assert replayed['operation_id'] == str(original_id)
    assert replayed['generation'] == pending['generation']
    async with async_session_maker() as session:
        operations = (await session.scalars(select(OrgBudgetOperation))).all()
        assert len(operations) == 1
        assert operations[0].plan == original_plan
    observed = await LiteLlmManager.get_team_members_financial_data(
        str(org_id), include_control_policy=True
    )
    assert observed['team_spend'] == before['team_spend']
    assert observed['team_max_budget'] == pytest.approx(before['team_spend'] + 10)
    member_spend = before['member_counters'][str(user_id)]['spend']
    assert observed['member_counters'][str(user_id)]['spend'] == member_spend
    assert observed['control_policy']['members'][str(user_id)][
        'max_budget'
    ] == pytest.approx(member_spend + 5)
    (await infer(live_proxy, key)).raise_for_status()
