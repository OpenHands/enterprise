from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from storage.lite_llm_manager import LiteLlmManager
from storage.org_budget_cycle_baseline import OrgBudgetCycleBaseline
from storage.org_budget_provisioning import provision_budget_member
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_budget_store import OrgBudgetStore
from storage.org_member import OrgMember
from storage.org_user_budget_override import OrgUserBudgetOverride
from storage.role import Role


@pytest.fixture
async def provision_case(create_org, create_user, async_session_maker, monkeypatch):
    org = create_org()
    user = create_user(current_org_id=org.id)
    monkeypatch.setattr(
        'storage.org_budget_provisioning.a_session_maker', async_session_maker
    )
    async with async_session_maker() as session:
        settings = OrgBudgetSettings(
            org_id=org.id, enabled=True, monthly_limit=10, default_user_monthly_limit=1
        )
        session.add(settings)
        await session.commit()
    return org.id, user.id


def financial(user_id=None, cap=1, spend=0, shared=False):
    return {
        'team_max_budget': 10,
        'team_spend': spend,
        'members': {}
        if user_id is None
        else {
            str(user_id): {
                'max_budget': cap,
                'spend': spend,
                'uses_shared_budget': shared,
            }
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'mode', ['personal', 'non-uuid', 'missing-settings', 'disabled']
)
async def test_skips_unmanaged_provisioning(provision_case, async_session_maker, mode):
    org_id, user_id = provision_case
    if mode == 'personal':
        user_id = org_id
    if mode == 'non-uuid':
        org_id = 'test-org'
    if mode == 'missing-settings':
        org_id = uuid4()
    if mode == 'disabled':
        async with async_session_maker() as session:
            settings = await OrgBudgetStore(session).get_settings(org_id)
            settings.enabled = False
            await session.commit()
    with patch.object(
        LiteLlmManager, '_get_team_members_financial_data', AsyncMock()
    ) as native:
        async with httpx.AsyncClient() as client:
            assert not await provision_budget_member(client, str(org_id), str(user_id))
    native.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('override', [None, 'limit', 'disabled'])
async def test_new_member_uses_policy_and_replaces_stale_baseline(
    provision_case, async_session_maker, override
):
    org_id, user_id = provision_case
    cap = 2 if override == 'limit' else 1
    async with async_session_maker() as session:
        store = OrgBudgetStore(session)
        settings = await store.get_settings(org_id)
        settings.user_cycle_start_spend = {str(user_id): 7}
        await store.record_cycle_baselines(
            org_id,
            settings.cycle_start_at,
            {str(user_id): 7},
            source=OrgBudgetCycleBaseline.SOURCE_MEMBER_ADDED,
            observed_at=datetime.now(UTC),
        )
        if override:
            session.add(
                OrgUserBudgetOverride(
                    org_id=org_id,
                    user_id=user_id,
                    monthly_limit=2 if override == 'limit' else None,
                    is_disabled=override == 'disabled',
                )
            )
        await session.commit()
    after = financial(
        user_id,
        cap=10 if override == 'disabled' else cap,
        shared=override == 'disabled',
    )
    with (
        patch.object(
            LiteLlmManager,
            '_get_team_members_financial_data',
            AsyncMock(side_effect=[financial(), after]),
        ),
        patch.object(LiteLlmManager, '_add_user_to_team', AsyncMock()) as add,
    ):
        async with httpx.AsyncClient() as client:
            assert await provision_budget_member(client, str(org_id), str(user_id))
        assert add.call_args.args[3] == (None if override == 'disabled' else cap)
    async with async_session_maker() as session:
        settings = await OrgBudgetStore(session).get_settings(org_id)
        assert settings.user_cycle_start_spend[str(user_id)] == 0
        assert (
            await OrgBudgetStore(session).get_cycle_baselines(
                org_id, settings.cycle_start_at
            )
        )[str(user_id)] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('native_exists', [True, False])
async def test_existing_app_member_uses_native_roster(
    provision_case, async_session_maker, native_exists
):
    org_id, user_id = provision_case
    async with async_session_maker() as session:
        role = Role(name='member', rank=1)
        session.add(role)
        await session.flush()
        session.add(
            OrgMember(
                org_id=org_id,
                user_id=user_id,
                role_id=role.id,
                llm_api_key='fixture-key',
            )
        )
        await session.commit()
    with (
        patch.object(
            LiteLlmManager,
            '_get_team_members_financial_data',
            AsyncMock(
                side_effect=[
                    financial(user_id) if native_exists else financial(),
                    financial(user_id),
                ]
            ),
        ),
        patch.object(LiteLlmManager, '_add_user_to_team', AsyncMock()) as add,
    ):
        async with httpx.AsyncClient() as client:
            assert await provision_budget_member(client, str(org_id), str(user_id))
    assert add.call_count == (0 if native_exists else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'after,error',
    [
        ({}, 'missing'),
        (financial(), 'missing'),
        ('wrong-cap', 'not applied'),
        ('shared', 'not applied'),
        ('invalid-spend', 'invalid spend'),
    ],
)
async def test_failed_readback_prevents_success(
    provision_case, async_session_maker, after, error
):
    org_id, user_id = provision_case
    if after == 'wrong-cap':
        after = financial(user_id, cap=10)
    if after == 'shared':
        after = financial(user_id, cap=10, shared=True)
    if after == 'invalid-spend':
        after = financial(user_id, spend=float('nan'))
    with (
        patch.object(
            LiteLlmManager,
            '_get_team_members_financial_data',
            AsyncMock(side_effect=[financial(), after]),
        ),
        patch.object(LiteLlmManager, '_add_user_to_team', AsyncMock()),
    ):
        async with httpx.AsyncClient() as client:
            with pytest.raises(RuntimeError, match=error):
                await provision_budget_member(client, str(org_id), str(user_id))
    async with async_session_maker() as session:
        settings = await OrgBudgetStore(session).get_settings(org_id)
        assert not await OrgBudgetStore(session).get_cycle_baselines(
            org_id, settings.cycle_start_at
        )


@pytest.mark.asyncio
@pytest.mark.parametrize('change_policy', [False, True])
async def test_network_calls_hold_no_settings_lock_and_policy_is_rechecked(
    provision_case, async_session_maker, change_policy
):
    org_id, user_id = provision_case

    async def add(*args):
        async with async_session_maker() as session:
            settings = (
                await session.execute(
                    select(OrgBudgetSettings)
                    .where(OrgBudgetSettings.org_id == org_id)
                    .with_for_update(nowait=True)
                )
            ).scalar_one()
            if change_policy:
                settings.default_user_monthly_limit = 2
            await session.commit()

    with (
        patch.object(
            LiteLlmManager,
            '_get_team_members_financial_data',
            AsyncMock(side_effect=[financial(), financial(user_id)]),
        ),
        patch.object(LiteLlmManager, '_add_user_to_team', AsyncMock(side_effect=add)),
    ):
        async with httpx.AsyncClient() as client:
            if change_policy:
                with pytest.raises(RuntimeError, match='policy changed'):
                    await provision_budget_member(client, str(org_id), str(user_id))
            else:
                assert await provision_budget_member(client, str(org_id), str(user_id))


@pytest.mark.asyncio
async def test_retry_updates_partial_native_membership_after_policy_change(
    provision_case,
):
    org_id, user_id = provision_case
    with (
        patch.object(
            LiteLlmManager,
            '_get_team_members_financial_data',
            AsyncMock(side_effect=[financial(user_id, cap=3), financial(user_id)]),
        ),
        patch.object(LiteLlmManager, '_add_user_to_team', AsyncMock()) as add,
        patch.object(LiteLlmManager, '_update_user_in_team', AsyncMock()) as update,
    ):
        async with httpx.AsyncClient() as client:
            assert await provision_budget_member(client, str(org_id), str(user_id))
    add.assert_not_called()
    assert update.call_args.args[3] == 1
