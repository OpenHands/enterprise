from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)
from pydantic import ValidationError

from server.routes.org_models import OrgBudgetSettingsUpdate
from tests.integration.budgets.adapter import (
    FIXED_REQUEST_COST,
    BudgetAdapterFactory,
    BudgetTestAdapter,
)

T = TypeVar('T')
LIMITS = st.integers(min_value=1, max_value=4)
USERS = st.integers(min_value=0, max_value=1)


class AsyncRunner:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def run(self, coroutine: Coroutine[Any, Any, T]) -> T:
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        return future.result(timeout=60)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()


class HealthyBudgetStateMachine(RuleBasedStateMachine):
    factory: BudgetAdapterFactory
    runner: AsyncRunner
    allow_requests = False
    allow_disabled_overrides = False

    def __init__(self) -> None:
        super().__init__()
        self.adapter: BudgetTestAdapter | None = None
        self.enabled = False
        self.organization_limit = 1.0
        self.default_user_limit = 1.0
        self.team_baseline = 0.0
        self.user_baselines = [0.0, 0.0]
        self.overrides: list[tuple[str, float | None]] = [
            ('default', None),
            ('default', None),
        ]
        self.provider_baseline = 0

    @initialize(organization_limit=LIMITS, default_user_limit=LIMITS)
    def initialize_budget(
        self, organization_limit: int, default_user_limit: int
    ) -> None:
        self.adapter = self.runner.run(self.factory.create())
        self.provider_baseline = self.runner.run(self.adapter.provider_calls())
        self.runner.run(
            self.adapter.configure_budget(organization_limit, default_user_limit)
        )
        self.enabled = True
        self.organization_limit = float(organization_limit)
        self.default_user_limit = float(default_user_limit)

    @rule(limit=LIMITS)
    def change_organization_limit(self, limit: int) -> None:
        self.runner.run(self._adapter.set_organization_limit(limit))
        self.organization_limit = float(limit)

    @rule(limit=LIMITS)
    def change_default_user_limit(self, limit: int) -> None:
        self.runner.run(self._adapter.set_default_user_limit(limit))
        self.default_user_limit = float(limit)

    @rule(user=USERS, limit=LIMITS)
    def set_positive_override(self, user: int, limit: int) -> None:
        self.runner.run(
            self._adapter.set_override(self._adapter.user_ids[user], float(limit))
        )
        self.overrides[user] = ('positive', float(limit))

    @precondition(lambda self: self.allow_disabled_overrides)
    @rule(user=USERS)
    def disable_member_limit(self, user: int) -> None:
        self.runner.run(
            self._adapter.set_override(
                self._adapter.user_ids[user], None, disabled=True
            )
        )
        self.overrides[user] = ('disabled', None)

    @rule(user=USERS)
    def delete_override(self, user: int) -> None:
        self.runner.run(self._adapter.delete_override(self._adapter.user_ids[user]))
        self.overrides[user] = ('default', None)

    @precondition(lambda self: self.allow_requests)
    @rule(user=USERS)
    def send_request(self, user: int) -> None:
        before = self.runner.run(self._adapter.financial_data())
        provider_before = self.runner.run(self._adapter.provider_calls())
        expected_admission = self._can_admit(user, before)

        response = self.runner.run(
            self._adapter.send_request(self._adapter.user_ids[user])
        )

        assert (response.status_code == 200) is expected_admission, response.text
        expected_spend = before['team_spend'] + (
            FIXED_REQUEST_COST if expected_admission else 0
        )
        after = self.runner.run(self._adapter.wait_for_spend(expected_spend))
        provider_after = self.runner.run(self._adapter.provider_calls())
        assert provider_after - provider_before == int(expected_admission)
        assert after['team_spend'] == expected_spend

    @rule()
    def maintenance_is_idempotent(self) -> None:
        before = self.runner.run(self._adapter.financial_data())
        self.runner.run(self._adapter.run_maintenance())
        after = self.runner.run(self._adapter.financial_data())
        assert after == before

    @rule(invalid_limit=st.integers(max_value=0))
    def reject_invalid_limits(self, invalid_limit: int) -> None:
        try:
            OrgBudgetSettingsUpdate(monthly_limit=invalid_limit)
        except ValidationError:
            return
        raise AssertionError(f'invalid monthly limit was accepted: {invalid_limit}')

    @invariant()
    def synchronized_policy_matches_litellm(self) -> None:
        if self.adapter is None:
            return
        state = self.runner.run(self.adapter.budget_state())
        financial_data = self.runner.run(self.adapter.financial_data())

        assert state['settings'].litellm_last_sync_status == 'success'
        assert financial_data['team_spend'] == sum(
            member['spend'] for member in financial_data['members'].values()
        )
        assert self.runner.run(
            self.adapter.provider_calls()
        ) - self.provider_baseline == int(financial_data['team_spend'])

        expected_team_cap = (
            self.team_baseline + self.organization_limit if self.enabled else None
        )
        assert financial_data['team_max_budget'] == expected_team_cap
        for user, user_id in enumerate(self.adapter.user_ids):
            assert financial_data['members'][str(user_id)]['max_budget'] == (
                self._expected_member_cap(user)
            )

    def teardown(self) -> None:
        self.runner.run(self.factory.close())

    @property
    def _adapter(self) -> BudgetTestAdapter:
        assert self.adapter is not None
        return self.adapter

    def _expected_member_cap(self, user: int) -> float | None:
        if self.overrides[user][0] == 'disabled':
            return None
        limit = (
            self.overrides[user][1]
            if self.overrides[user][0] == 'positive'
            else self.default_user_limit
        )
        assert limit is not None
        return self.user_baselines[user] + limit

    def _can_admit(self, user: int, financial_data: dict[str, Any]) -> bool:
        if (
            self.enabled
            and financial_data['team_spend']
            >= self.team_baseline + self.organization_limit
        ):
            return False
        member = financial_data['members'][str(self._adapter.user_ids[user])]
        member_cap = self._expected_member_cap(user)
        return member_cap is None or member['spend'] < member_cap


def run_budget_state_machine(
    budget_adapter_factory: BudgetAdapterFactory,
    *,
    allow_requests: bool,
    allow_disabled_overrides: bool = False,
) -> None:
    runner = AsyncRunner()

    class Scenario(HealthyBudgetStateMachine):
        factory = budget_adapter_factory

    Scenario.runner = runner
    Scenario.allow_requests = allow_requests
    Scenario.allow_disabled_overrides = allow_disabled_overrides
    try:
        run_state_machine_as_test(
            Scenario,
            settings=settings(
                max_examples=int(os.getenv('BUDGET_STATE_MACHINE_EXAMPLES', '8')),
                stateful_step_count=int(os.getenv('BUDGET_STATE_MACHINE_STEPS', '10')),
                deadline=None,
                suppress_health_check=[HealthCheck.too_slow],
            ),
        )
    finally:
        runner.run(budget_adapter_factory.close())
        runner.close()


def test_healthy_budget_state_machine(
    budget_adapter_factory: BudgetAdapterFactory,
) -> None:
    run_budget_state_machine(budget_adapter_factory, allow_requests=False)
