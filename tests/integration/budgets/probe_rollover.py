from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.services.org_budget_service import OrgBudgetService
from storage.org_budget_settings import OrgBudgetSettings
from tests.integration.budgets.adapter import BudgetTestAdapter


@pytest.mark.asyncio
async def test_delayed_rollover_worker_cannot_renew_allowance_twice(
    budget_adapter: BudgetTestAdapter,
    async_session_maker: async_sessionmaker[AsyncSession],
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
    assert (await adapter.send_request(adapter.user_ids[0])).status_code == 200
    await adapter.wait_for_spend(1)
    async with async_session_maker() as delayed_session:
        stale = (
            await delayed_session.execute(
                select(OrgBudgetSettings).where(
                    OrgBudgetSettings.org_id == adapter.org_id
                )
            )
        ).scalar_one()
        await delayed_session.commit()
        delayed = OrgBudgetService(delayed_session)
        assert (await adapter.run_maintenance())['cycle_rolled'] is True
        assert (await adapter.send_request(adapter.user_ids[0])).status_code == 200
        await adapter.wait_for_spend(2)
        # Resume a worker that loaded the old anchor before the winning rollover.
        result = await delayed._get_financial_snapshot(
            adapter.org_id, stale, allow_stale=False
        )
        assert result.snapshot is not None
        rolled = await delayed._roll_cycle_if_needed(stale, [], [], result.snapshot)
        await delayed_session.commit()
        assert rolled is False, (
            'a stale worker must observe the already-completed rollover'
        )
    state = await adapter.budget_state()
    assert state['current_spend'] == 1
