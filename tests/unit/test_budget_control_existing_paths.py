"""Existing budget API and maintenance paths must respect explicit ownership."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from server.routes.org_models import OrgBudgetSettingsUpdate
from server.services.org_budget_service import OrgBudgetService
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_user_budget_override import OrgUserBudgetOverride


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['external', 'needs_adoption'])
@pytest.mark.parametrize(
    'action',
    [
        'maintenance',
        'settings',
        'override',
        'delete_override',
        'sync',
        'roll',
        'repair',
    ],
)
async def test_existing_budget_writers_preserve_unowned_policy(
    create_org, async_session_maker, mode, action
):
    org_id = create_org().id
    async with async_session_maker() as session:
        settings = OrgBudgetSettings(
            org_id=org_id,
            control_mode=mode,
            enabled=True,
            monthly_limit=1,
            default_user_monthly_limit=1,
            cycle_start_at=datetime(2026, 8, 1, tzinfo=UTC),
            cycle_start_spend=302.55,
            user_cycle_start_spend={},
        )
        session.add(settings)
        await session.commit()
        service = OrgBudgetService(session)
        with (
            patch(
                'storage.lite_llm_manager.LiteLlmManager.update_team', AsyncMock()
            ) as team,
            patch(
                'storage.lite_llm_manager.LiteLlmManager.update_user_in_team',
                AsyncMock(),
            ) as member,
            patch(
                'storage.lite_llm_manager.LiteLlmManager.add_user_to_team', AsyncMock()
            ) as add,
            patch(
                'storage.lite_llm_manager.LiteLlmManager.get_team_members_financial_data',
                AsyncMock(),
            ) as read,
        ):
            if action == 'maintenance':
                result = await service.run_budget_maintenance(org_id)
                assert result['skipped'] == mode
            elif action == 'sync':
                assert (
                    await service._sync_litellm_budgets(
                        org_id, settings, [], clear_disabled=True
                    )
                    is None
                )
            else:
                with pytest.raises(HTTPException) as error:
                    if action == 'settings':
                        await service.update_budget_settings(
                            org_id, OrgBudgetSettingsUpdate(monthly_limit=500)
                        )
                    elif action == 'override':
                        await service.upsert_user_override(org_id, uuid4(), 100, False)
                    elif action == 'delete_override':
                        await service.delete_user_override(org_id, uuid4())
                    elif action == 'roll':
                        await service._roll_cycle_if_needed(settings, [], [], None)
                    else:
                        await service._repair_missing_members_for_cycle(
                            org_id, settings, [], None
                        )
                assert error.value.status_code == 409
            for mock in (team, member, add, read):
                mock.assert_not_awaited()
        await session.commit()
        await session.refresh(settings)
        assert settings.monthly_limit == 1
        assert settings.default_user_monthly_limit == 1
        assert settings.cycle_start_spend == 302.55
        assert settings.user_cycle_start_spend == {}
        assert settings.cycle_start_at == datetime(2026, 8, 1, tzinfo=UTC)
        assert await session.scalar(select(OrgUserBudgetOverride)) is None
