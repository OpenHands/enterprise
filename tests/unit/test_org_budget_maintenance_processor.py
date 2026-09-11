from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from server.constants import ORG_SETTINGS_VERSION
from server.maintenance_task_processor.org_budget_maintenance_processor import (
    OrgBudgetMaintenanceProcessor,
)
from server.services.org_budget_service import _current_cycle_start
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
    financial_data = {
        'team_max_budget': 1900.0,
        'team_spend': 900.0,
        'members': {},
    }

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

    assert result == {
        'processed': 1,
        'failed': 0,
        'error_count': 0,
        'errors': [],
        'success': True,
    }

    async with async_session_maker() as session:
        settings = await session.scalar(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == org_id)
        )

    assert settings is not None
    assert settings.cycle_start_at.replace(tzinfo=UTC) == _current_cycle_start(now, 1)
    assert settings.cycle_start_spend == 900.0
    assert settings.litellm_last_sync_status == 'success'
    assert settings.litellm_last_sync_at is not None


@pytest.mark.asyncio
async def test_processor_counts_logical_sync_error_as_failed(async_session_maker):
    """When run_budget_maintenance returns status='error', the processor must
    count the org as failed and report success=False so run_tasks marks the
    task ERROR instead of COMPLETED.
    """
    org_id = uuid4()
    async with async_session_maker() as session:
        session.add(
            Org(
                id=org_id,
                name='Logical Fail Org',
                org_version=ORG_SETTINGS_VERSION,
            )
        )
        session.add(
            OrgBudgetSettings(
                org_id=org_id,
                reset_day=1,
                monthly_limit=100.0,
                cycle_start_at=datetime.now(UTC).replace(day=1, tzinfo=None),
                cycle_start_spend=0.0,
            )
        )
        await session.commit()

    task = MaintenanceTask(
        status=MaintenanceTaskStatus.PENDING,
        processor_type='org_budget_maintenance',
        processor_json='{"org_ids": ["' + str(org_id) + '"]}',
    )
    processor = OrgBudgetMaintenanceProcessor(org_ids=[str(org_id)])

    async def _fail_run_budget_maintenance(self, _org_uuid):
        return {
            'status': 'error',
            'reason': 'litellm_spend_unavailable',
            'sync_status': 'error',
            'sync_error': 'no spend data',
            'drift': [],
        }

    with (
        patch(
            'server.maintenance_task_processor.org_budget_maintenance_processor.a_session_maker',
            async_session_maker,
        ),
        patch(
            'server.maintenance_task_processor.org_budget_maintenance_processor.OrgBudgetService.run_budget_maintenance',
            _fail_run_budget_maintenance,
        ),
    ):
        result = await processor(task)

    assert result['success'] is False
    assert result['failed'] == 1
    assert result['processed'] == 0
    assert result['error_count'] == 1
    assert result['errors'][0]['error'] == 'litellm_spend_unavailable'


@pytest.mark.asyncio
async def test_processor_mixed_success_and_failure_reports_success_false(
    async_session_maker,
):
    """A single failed org among several healthy ones must flip success=False."""
    healthy_org = uuid4()
    failing_org = uuid4()
    async with async_session_maker() as session:
        for oid in (healthy_org, failing_org):
            session.add(
                Org(id=oid, name=f'Org {oid}', org_version=ORG_SETTINGS_VERSION)
            )
            session.add(
                OrgBudgetSettings(
                    org_id=oid,
                    reset_day=1,
                    monthly_limit=100.0,
                    cycle_start_at=datetime.now(UTC).replace(day=1, tzinfo=None),
                    cycle_start_spend=0.0,
                )
            )
        await session.commit()

    task = MaintenanceTask(
        status=MaintenanceTaskStatus.PENDING,
        processor_type='org_budget_maintenance',
        processor_json='{"org_ids": ["'
        + str(healthy_org)
        + '", "'
        + str(failing_org)
        + '"]}',
    )
    processor = OrgBudgetMaintenanceProcessor(
        org_ids=[str(healthy_org), str(failing_org)]
    )

    async def _run_budget_maintenance(self, org_uuid):
        if org_uuid == failing_org:
            return {
                'status': 'error',
                'reason': 'litellm_membership_repair_failed',
                'drift': [],
            }
        return {'status': 'success', 'drift': []}

    with (
        patch(
            'server.maintenance_task_processor.org_budget_maintenance_processor.a_session_maker',
            async_session_maker,
        ),
        patch(
            'server.maintenance_task_processor.org_budget_maintenance_processor.OrgBudgetService.run_budget_maintenance',
            _run_budget_maintenance,
        ),
    ):
        result = await processor(task)

    assert result['success'] is False
    assert result['processed'] == 1
    assert result['failed'] == 1
    assert result['error_count'] == 1
