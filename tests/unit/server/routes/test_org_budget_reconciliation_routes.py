from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Response, status

from server.routes.org_models import (
    OrgBudgetSettingsUpdate,
    OrgBudgetUserOverrideUpdate,
)
from server.routes.orgs import (
    delete_org_budget_override,
    update_org_budget_settings,
    upsert_org_budget_override,
)


def _budget_state(reconciliation_state: str) -> dict:
    now = datetime.now(UTC)
    settings = SimpleNamespace(
        enabled=True,
        monthly_limit=1000.0,
        litellm_last_sync_at=now,
        litellm_last_sync_status='error',
        litellm_last_sync_error='team_budget_mismatch',
        reset_day=1,
        slack_channel=None,
        slack_team_id=None,
        default_user_monthly_limit=300.0,
    )
    return {
        'settings': settings,
        'thresholds': [],
        'cycle': SimpleNamespace(start_at=now, end_at=now + timedelta(days=30)),
        'current_spend': 2.29,
        'spend_status': 'live',
        'spend_observed_at': now,
        'unmapped_spend': 0.0,
        'unmapped_member_count': 0,
        'users': [],
        'users_total': 0,
        'users_page': 1,
        'users_per_page': 50,
        'reconciliation_state': reconciliation_state,
        'reconciliation_error': 'team_budget_mismatch',
        'desired_team_max_budget': 1002.0,
        'applied_team_max_budget': 2.05,
        'budget_policy_matches': False,
        'applied_at': None,
        'applied_policy_observed_at': now,
    }


@pytest.mark.asyncio
async def test_org_budget_write_returns_503_with_degraded_state():
    service = AsyncMock()
    service.update_budget_settings.return_value = _budget_state('degraded')
    response = Response()

    result = await update_org_budget_settings(
        org_id=uuid4(),
        update=OrgBudgetSettingsUpdate(monthly_limit=1000.0),
        response=response,
        user_id=str(uuid4()),
        budget_service=service,
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert result.reconciliation_state == 'degraded'
    assert result.desired_team_max_budget == 1002.0
    assert result.applied_team_max_budget == 2.05


@pytest.mark.asyncio
async def test_member_budget_write_returns_503_with_degraded_state():
    service = AsyncMock()
    user_id = uuid4()
    service.get_user_budget_row.return_value = {
        'user_id': str(user_id),
        'effective_monthly_limit': 300.0,
        'reconciliation_state': 'degraded',
        'reconciliation_error': 'member_budget_mismatch',
    }
    response = Response()

    result = await upsert_org_budget_override(
        org_id=uuid4(),
        user_id=str(user_id),
        update=OrgBudgetUserOverrideUpdate(
            monthly_limit=300.0,
            is_disabled=False,
        ),
        response=response,
        current_user_id=str(uuid4()),
        budget_service=service,
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert result.reconciliation_state == 'degraded'


@pytest.mark.asyncio
async def test_member_budget_delete_returns_503_with_degraded_state():
    service = AsyncMock()
    service.get_reconciliation_state.return_value = 'degraded'
    response = Response()

    await delete_org_budget_override(
        org_id=uuid4(),
        user_id=str(uuid4()),
        response=response,
        current_user_id=str(uuid4()),
        budget_service=service,
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
