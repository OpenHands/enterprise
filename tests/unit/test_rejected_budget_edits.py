"""Rejected edits retain policy rows while persisting recovery metadata."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from server.routes.org_models import OrgBudgetSettingsUpdate, OrgBudgetThresholdUpdate
from server.services.org_budget_service import (
    BudgetChangeRejectedError,
    OrgBudgetService,
)
from storage.org_budget_cycle_baseline import OrgBudgetCycleBaseline
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_budget_store import OrgBudgetStore
from storage.org_budget_threshold import OrgBudgetThreshold
from storage.org_member import OrgMember
from storage.org_user_budget_override import OrgUserBudgetOverride
from storage.role import Role
from tests.unit.test_org_budget_service import _financial_data


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [True, False])
@pytest.mark.parametrize('operation', ['settings', 'upsert', 'delete'])
@pytest.mark.parametrize('readback', ['matching', 'drift', 'unavailable'])
async def test_rejected_edit_preserves_policy_and_commits_failure(
    enabled, operation, readback, async_session_maker, create_org, create_user
):
    org = create_org()
    user = create_user(current_org_id=org.id)
    anchor = datetime(2026, 9, 1, tzinfo=UTC)
    async with async_session_maker() as session:
        role = Role(name='owner', rank=0)
        session.add(role)
        await session.flush()
        session.add_all(
            [
                OrgMember(
                    org_id=org.id,
                    user_id=user.id,
                    role_id=role.id,
                    llm_api_key='test-only',
                ),
                OrgBudgetSettings(
                    org_id=org.id,
                    enabled=enabled,
                    monthly_limit=100,
                    default_user_monthly_limit=30,
                    reset_day=1,
                    cycle_start_at=anchor,
                    cycle_start_spend=8,
                    user_cycle_start_spend={str(user.id): 8},
                    litellm_known_member_ids=[str(user.id)],
                    litellm_last_sync_status='success',
                ),
                OrgUserBudgetOverride(
                    org_id=org.id, user_id=user.id, monthly_limit=10, is_disabled=False
                ),
                OrgBudgetThreshold(
                    org_id=org.id,
                    percentage=80,
                    email_enabled=False,
                    slack_enabled=False,
                ),
            ]
        )
        await session.flush()
        await OrgBudgetStore(session).record_cycle_baselines(
            org.id,
            anchor,
            {str(user.id): 8},
            source=OrgBudgetCycleBaseline.SOURCE_ENABLEMENT,
            observed_at=anchor,
        )
        await session.commit()
    native = _financial_data(
        team_spend=12,
        team_max_budget=108 if enabled else None,
        members={str(user.id): (12, 17 if readback == 'drift' else 18, False)},
    )
    with (
        patch(
            'server.services.org_budget_service.LiteLlmManager.set_team_blocked',
            AsyncMock(side_effect=RuntimeError('block unavailable')),
        ),
        patch(
            'server.services.org_budget_service.LiteLlmManager.block_team',
            AsyncMock(side_effect=RuntimeError('fallback unavailable')),
        ),
        patch(
            'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
            AsyncMock(
                return_value=native,
                side_effect=RuntimeError('read unavailable')
                if readback == 'unavailable'
                else None,
            ),
        ),
        patch(
            'server.services.org_budget_service.LiteLlmManager.update_team', AsyncMock()
        ) as team_write,
        patch(
            'server.services.org_budget_service.LiteLlmManager.update_user_in_team',
            AsyncMock(),
        ) as member_write,
    ):
        async with async_session_maker() as session:
            service = OrgBudgetService(session)
            with pytest.raises(BudgetChangeRejectedError) as rejected:
                if operation == 'settings':
                    await service.update_budget_settings(
                        org.id,
                        OrgBudgetSettingsUpdate(
                            monthly_limit=20,
                            default_user_monthly_limit=10,
                            reset_day=15,
                            thresholds=[
                                OrgBudgetThresholdUpdate(
                                    percentage=90,
                                    email_enabled=False,
                                    slack_enabled=False,
                                )
                            ],
                        ),
                    )
                elif operation == 'upsert':
                    await service.upsert_user_override(org.id, user.id, 5, False)
                else:
                    await service.delete_user_override(org.id, user.id)
            assert rejected.value.previous_policy_verified is (readback == 'matching')
            assert rejected.value.detail['code'] == 'budget_change_rejected'
            await session.commit()
        team_write.assert_not_awaited()
        member_write.assert_not_awaited()
    async with async_session_maker() as session:
        store = OrgBudgetStore(session)
        settings = await store.get_settings(org.id)
        assert (
            settings.enabled,
            settings.monthly_limit,
            settings.default_user_monthly_limit,
        ) == (enabled, 100, 30)
        assert settings.reset_day == 1
        assert settings.cycle_start_at == anchor
        assert settings.next_reset_at is None
        assert settings.cycle_start_spend == 8
        assert settings.user_cycle_start_spend == {str(user.id): 8}
        assert settings.litellm_last_sync_status == 'error'
        assert 'admission_fallback_failed' in settings.litellm_last_sync_error
        assert await store.get_cycle_baselines(org.id, anchor) == {str(user.id): 8}
        override = await store.get_override(org.id, user.id)
        assert (override.monthly_limit, override.is_disabled) == (10, False)
        thresholds = (
            (
                await session.execute(
                    select(OrgBudgetThreshold).where(
                        OrgBudgetThreshold.org_id == org.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [threshold.percentage for threshold in thresholds] == [80]
