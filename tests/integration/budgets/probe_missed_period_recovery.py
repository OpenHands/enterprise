from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from tests.integration.budgets.adapter import BudgetTestAdapter


@pytest.mark.asyncio
async def test_missed_period_recovery_does_not_renew_allowance_again(
    budget_adapter: BudgetTestAdapter,
) -> None:
    """A multi-period gap must be recovered without renewing the cap twice.

    An org whose cycle anchor is several periods old (a paused CronJob or an
    outage) recovers on the first maintenance run: the cycle rolls once and the
    native cap is re-anchored to the current cumulative spend. A second run in
    the same period must be a no-op. If the roll only advanced one period, the
    anchor would still be behind, so the second run would roll again, re-anchor
    the baseline to the newer cumulative total, forgive the spend incurred since
    recovery, and grant a fresh allowance. This exercises the exact regression
    reported on PR #403 against real LiteLLM and a real cost path.
    """
    adapter = budget_adapter
    now = datetime.now(UTC)
    # Anchor the cycle several periods in the past so recovery must catch up a gap.
    past = (now.replace(day=1) - timedelta(days=70)).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    class BeforeOutage(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return past

    with patch('server.services.org_budget_service.datetime', BeforeOutage):
        await adapter.configure_budget(5, 3)

    # Spend $1, then recover: the first run rolls the stale cycle exactly once.
    assert (await adapter.send_request(adapter.user_ids[0])).status_code == 200
    await adapter.wait_for_spend(1)
    assert (await adapter.run_maintenance())['cycle_rolled'] is True
    cap_after_recovery = (await adapter.financial_data())['team_max_budget']

    # Spend another $1 after recovery, then run maintenance again in the same
    # period. It must not roll again or renew the allowance.
    assert (await adapter.send_request(adapter.user_ids[0])).status_code == 200
    await adapter.wait_for_spend(2)
    second = await adapter.run_maintenance()
    state = await adapter.budget_state()
    native = await adapter.financial_data()

    assert second['cycle_rolled'] is False, (
        'a second run in the same period must not roll the cycle again'
    )
    assert state['current_spend'] == 1, (
        'spend incurred since recovery must be preserved, not forgiven'
    )
    assert native['team_max_budget'] == cap_after_recovery, (
        'the native cap must not be renewed on a repeat maintenance run'
    )
