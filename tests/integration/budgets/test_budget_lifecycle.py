from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from storage.org_budget_settings import OrgBudgetSettings
from tests.integration.budgets.adapter import BudgetTestAdapter


@pytest.mark.asyncio
async def test_limit_edits_keep_existing_spend(
    budget_adapter: BudgetTestAdapter,
) -> None:
    adapter = budget_adapter
    await adapter.configure_budget(5, 3)
    assert (await adapter.send_request(adapter.user_ids[0])).status_code == 200
    await adapter.wait_for_spend(1)
    await adapter.set_override(adapter.user_ids[1], 2)
    await adapter.set_organization_limit(6)
    await adapter.set_default_user_limit(4)
    state = await adapter.budget_state()
    assert state['current_spend'] == 1
    native = await adapter.financial_data()
    assert native['team_max_budget'] == 6
    assert native['members'][str(adapter.user_ids[0])]['max_budget'] == 4
    assert native['members'][str(adapter.user_ids[1])]['max_budget'] == 2
    await adapter.set_organization_limit(1)
    before = await adapter.provider_calls()
    response = await adapter.send_request(adapter.user_ids[0])
    assert response.status_code in {401, 403, 429}, response.text
    assert await adapter.provider_calls() == before
    assert (await adapter.financial_data())['team_spend'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('org_enabled', [True, False])
async def test_month_rollover_renews_once(
    budget_adapter: BudgetTestAdapter, org_enabled: bool
) -> None:
    adapter = budget_adapter
    now = datetime.now(UTC)
    previous = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous = (
        previous.replace(year=now.year - 1, month=12)
        if now.month == 1
        else previous.replace(month=now.month - 1)
    )

    class PreviousMonth(datetime):
        @classmethod
        def now(cls, tz=None):
            return previous

    with patch('server.services.org_budget_service.datetime', PreviousMonth):
        await adapter.configure_budget(5, 3)
        if not org_enabled:
            await adapter.disable_budget()
    assert (await adapter.send_request(adapter.user_ids[0])).status_code == 200
    await adapter.wait_for_spend(1)
    first = await adapter.run_maintenance()
    assert first['cycle_rolled'] is True
    settings = (
        await adapter.session.execute(
            select(OrgBudgetSettings).where(OrgBudgetSettings.org_id == adapter.org_id)
        )
    ).scalar_one()
    baseline = settings.cycle_start_spend
    member_baselines = dict(settings.user_cycle_start_spend)
    assert baseline == 1
    native = await adapter.financial_data()
    assert native['team_max_budget'] == (6 if org_enabled else None)
    assert native['members'][str(adapter.user_ids[0])]['max_budget'] == 4
    assert (await adapter.send_request(adapter.user_ids[0])).status_code == 200
    await adapter.wait_for_spend(2)
    second = await adapter.run_maintenance()
    assert second['cycle_rolled'] is False
    await adapter.session.refresh(settings)
    assert settings.cycle_start_spend == baseline
    assert settings.user_cycle_start_spend == member_baselines
    assert (await adapter.budget_state())['current_spend'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'old_day,new_day,expected_end', [(1, 15, '2026-09-15'), (15, 1, '2026-10-01')]
)
async def test_reset_day_edits_keep_existing_spend(
    budget_adapter: BudgetTestAdapter, old_day, new_day, expected_end
) -> None:
    adapter = budget_adapter
    boundary = datetime.fromisoformat(expected_end).replace(tzinfo=UTC)
    clock = datetime(2026, 9, 10, tzinfo=UTC)

    class BudgetClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock

    with patch('server.services.org_budget_service.datetime', BudgetClock):
        await adapter.update_settings(
            enabled=True,
            monthly_limit=1,
            default_user_monthly_limit=1,
            reset_day=old_day,
        )
        assert (await adapter.send_request(adapter.user_ids[0])).status_code == 200
        await adapter.wait_for_spend(1)
        before = await adapter.budget_state()
        native_before = await adapter.financial_data()
        member = str(adapter.user_ids[0])
        after = await adapter.update_settings(reset_day=new_day)
        assert after['current_spend'] == 1
        assert after['cycle'].start_at == before['cycle'].start_at
        assert after['cycle'].end_at == boundary
        native = await adapter.financial_data()
        assert native['team_max_budget'] == native_before['team_max_budget'] == 1
        assert (
            native['members'][member]['max_budget']
            == native_before['members'][member]['max_budget']
        )
        assert not (await adapter.run_maintenance())['cycle_rolled']
        assert (await adapter.budget_state())['current_spend'] == 1

        clock = boundary - timedelta(seconds=1)
        assert not (await adapter.run_maintenance())['cycle_rolled']
        calls = await adapter.provider_calls()
        assert (await adapter.send_request(adapter.user_ids[0])).status_code in {
            401,
            403,
            429,
        }
        assert await adapter.provider_calls() == calls

        clock = boundary
        assert (await adapter.update_settings(reset_day=new_day))[
            'cycle'
        ].end_at == boundary
        assert (await adapter.run_maintenance())['cycle_rolled']
        assert (await adapter.budget_state())['current_spend'] == 0
        assert (await adapter.send_request(adapter.user_ids[0])).status_code == 200
        await adapter.wait_for_spend(2)
        assert not (await adapter.run_maintenance())['cycle_rolled']
        assert (await adapter.budget_state())['current_spend'] == 1
        calls = await adapter.provider_calls()
        assert (await adapter.send_request(adapter.user_ids[0])).status_code in {
            401,
            403,
            429,
        }
        assert await adapter.provider_calls() == calls
