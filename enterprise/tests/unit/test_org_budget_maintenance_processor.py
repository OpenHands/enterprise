from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from server.constants import ORG_SETTINGS_VERSION
from server.maintenance_task_processor.org_budget_maintenance_processor import (
    OrgBudgetMaintenanceProcessor,
)
from server.services.org_budget_service import _current_cycle_start
from sqlalchemy import select
from storage.maintenance_task import MaintenanceTask, MaintenanceTaskStatus
from storage.org import Org
from storage.org_budget_settings import OrgBudgetSettings


@pytest.mark.asyncio
async def test_processor_persists_budget_maintenance_updates(async_session_maker):
    org_id = uuid4()
    now = datetime.now(UTC)
    stale_cycle_start = _current_cycle_start(now - timedelta(days=40), 1)

    async with async_session_maker() as session:
        session.add(
            Org(
                id=org_id,
                name=f'test-org-{org_id}',
                org_version=ORG_SETTINGS_VERSION,
                enable_proactive_conversation_starters=True,
            )
        )
        session.add(
            OrgBudgetSettings(
                org_id=org_id,
                enabled=True,
                reset_day=1,
                monthly_limit=1000.0,
                default_user_monthly_limit=None,
                slack_channel=None,
                slack_team_id=None,
                cycle_start_at=stale_cycle_start,
                cycle_start_spend=0.0,
            )
        )
        await session.commit()

    processor = OrgBudgetMaintenanceProcessor(org_ids=[str(org_id)])
    task = MaintenanceTask(
        status=MaintenanceTaskStatus.WORKING,
        processor_type='',
        processor_json='{}',
        delay=0,
    )
    financial_data = {'team_spend': 900.0, 'members': {}}

    with (
        patch(
            'server.maintenance_task_processor.org_budget_maintenance_processor.a_session_maker',
            async_session_maker,
        ),
        patch(
            'server.services.org_budget_service.LiteLlmManager.get_team_members_financial_data',
            AsyncMock(return_value=financial_data),
        ),
        patch(
            'server.services.org_budget_service.LiteLlmManager.update_team',
            AsyncMock(),
        ),
    ):
        result = await processor(task)

    assert result == {'processed': 1, 'error_count': 0, 'errors': []}

    async with async_session_maker() as session:
        settings = await session.scalar(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == org_id)
        )

    assert settings is not None
    assert settings.cycle_start_at.replace(tzinfo=UTC) == _current_cycle_start(now, 1)
    assert settings.cycle_start_spend == 900.0
    assert settings.litellm_last_sync_status == 'success'
    assert settings.litellm_last_sync_at is not None
