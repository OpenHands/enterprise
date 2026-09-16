"""OHE-3257: real LiteLLM upgrade regression test for stale caps and shared keys.

This probe exercises the exact legacy-upgrade fixture described in
https://linear.app/all-hands-ai/issue/OHE-3257:

* 11 Enterprise members with empty migration-149 baselines.
* Migration 156 marks all members known.
* Only one correctly attributed LiteLLM member.
* One administrator key copied to other rows.
* Stale cap ``$2.05264885``, spend above the cap, org limit ``$1,000``,
  user override ``$300``.

The deterministic provider charges exactly ``$1.00`` per accepted request
(10 prompt + 5 completion tokens at ``$0.05``/``$0.10`` per token), so the
exact production spend of ``$2.2908277`` is not reproducible.  The probe
instead sends three requests (``$3.00``) to create an over-cap state — the
critical invariant is that the stale cap is below current spend and gets
replaced by the correct cap after maintenance.

Acceptance criteria (from the issue):

1. Every user receives their own key.           -- xfail until #353 (OHE-3252)
2. Baselines remain stable on later syncs.      -- passing (#347 merged)
3. Desired and actual caps match after readback. -- passing
4. Partial failures resume without renewing allowance. -- passing (core behavior)
5. Unresolved reconciliation fails the process/job.   -- xfail until #356 (OHE-3254)
"""

from __future__ import annotations

import pytest

from storage.lite_llm_manager import LiteLlmManager
from tests.integration.budgets.adapter import UpgradeBudgetTestAdapter

# Exact values from the production incident (OHE-3257 fixture).
STALE_TEAM_CAP = 2.05264885
ORG_LIMIT = 1000.0
USER_OVERRIDE = 300.0
MEMBER_COUNT = 11


async def _seed_over_cap_spend(adapter: UpgradeBudgetTestAdapter) -> float:
    """Send inference requests until team spend exceeds the stale cap.

    The deterministic provider charges $1.00 per request, so three requests
    produce $3.00 — above the stale cap of $2.05264885.  The exact production
    spend ($2.2908277) is not reproducible with integer-cost requests, but
    the over-cap relationship is what the regression test exercises.
    """
    # Member 0 owns the only valid key; send requests as that member.
    for _ in range(3):
        response = await adapter.send_request(adapter.user_ids[0])
        assert response.status_code == 200, response.text

    financial = await adapter.wait_for_spend(3.0)
    assert financial['team_spend'] == 3.0
    assert financial['team_spend'] > STALE_TEAM_CAP  # over-cap state
    return financial['team_spend']


# ---------------------------------------------------------------------------
# Acceptance criterion 1: Every user receives their own key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason='requires OHE-3252 (#353) ManagedLlmKeyOwnershipProcessor to merge',
    strict=True,
)
async def test_upgrade_assigns_unique_owned_keys(
    upgrade_adapter: UpgradeBudgetTestAdapter,
) -> None:
    """After key-ownership repair, every member owns a distinct key.

    Depends on #353 (OHE-3252) which adds ``ManagedLlmKeyOwnershipProcessor``
    and migration 161.  The processor verifies each member's key with
    ``verify_existing_key_strict`` and regenerates keys that belong to another
    user.
    """
    adapter = upgrade_adapter
    await _seed_over_cap_spend(adapter)

    # Run the managed-key ownership repair processor (from #353).
    # This import will fail until #353 merges, which is why the test is xfail.
    from server.maintenance_task_processor.managed_llm_key_ownership_processor import (
        ManagedLlmKeyOwnershipProcessor,
        ManagedLlmKeyOwnershipTarget,
    )

    targets = [
        ManagedLlmKeyOwnershipTarget(org_id=str(adapter.org_id), user_id=str(uid))
        for uid in adapter.all_user_ids
    ]
    processor = ManagedLlmKeyOwnershipProcessor(targets=targets)
    # The processor expects a MaintenanceTask; pass a dummy.
    result = await processor(task=None)  # type: ignore[arg-type]
    assert result.get('error_count', 0) == 0, result

    # Every member should now own a distinct key.
    members = await adapter.get_all_members()
    assert len(members) == MEMBER_COUNT

    keys: set[str] = set()
    for member in members:
        key = member.llm_api_key.get_secret_value()
        keys.add(key)
        # Verify the key belongs to this member via LiteLLM.
        owned = await LiteLlmManager.verify_existing_key(
            key,
            str(member.user_id),
            str(adapter.org_id),
        )
        assert owned, f'member {member.user_id} does not own their key'

    assert len(keys) == MEMBER_COUNT, 'keys are not unique across members'


# ---------------------------------------------------------------------------
# Acceptance criterion 2: Baselines remain stable on later syncs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_baseline_recovery_is_idempotent(
    upgrade_adapter: UpgradeBudgetTestAdapter,
) -> None:
    """Missing migration-149 baselines are recovered once and stay stable.

    PR #347 (merged) anchors missing legacy baselines to live cumulative
    spend.  A second maintenance run must not change the recovered baselines.
    """
    adapter = upgrade_adapter
    await _seed_over_cap_spend(adapter)

    # First maintenance run: recovers missing baselines from live spend.
    await adapter.run_maintenance()
    first_settings = await adapter.get_settings()
    first_baselines: dict[str, float] = dict(
        first_settings.user_cycle_start_spend or {}
    )
    assert len(first_baselines) == MEMBER_COUNT, (
        f'expected {MEMBER_COUNT} baselines, got {len(first_baselines)}'
    )

    # Second maintenance run: baselines must not change.
    await adapter.run_maintenance()
    second_settings = await adapter.get_settings()
    second_baselines: dict[str, float] = dict(
        second_settings.user_cycle_start_spend or {}
    )
    assert second_baselines == first_baselines, (
        'baselines changed on second sync — recovery is not idempotent'
    )


# ---------------------------------------------------------------------------
# Acceptance criterion 3: Desired and actual caps match after readback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_caps_match_after_readback(
    upgrade_adapter: UpgradeBudgetTestAdapter,
) -> None:
    """After maintenance, LiteLLM caps match the desired budget policy.

    Team cap = cycle_start_spend + org_limit.
    Override member cap = baseline + user_override.
    Default member cap = baseline + default_user_monthly_limit.
    """
    adapter = upgrade_adapter
    await _seed_over_cap_spend(adapter)

    await adapter.run_maintenance()
    settings = await adapter.get_settings()
    financial = await adapter.financial_data()

    # Team cap: cycle_start_spend + monthly_limit.
    expected_team = settings.cycle_start_spend + settings.monthly_limit
    actual_team = financial['team_max_budget']
    assert actual_team is not None
    assert abs(actual_team - expected_team) <= 1e-6, (
        f'team cap mismatch: expected {expected_team}, got {actual_team}'
    )

    # Override member (member 0): cap = baseline + override.
    override_uid = adapter.user_ids[0]
    override = await adapter.get_override(override_uid)
    assert override is not None
    assert override.monthly_limit == USER_OVERRIDE

    member_data = financial['members'][str(override_uid)]
    baseline = settings.user_cycle_start_spend[str(override_uid)]
    expected_member_cap = baseline + USER_OVERRIDE
    assert not member_data['uses_shared_budget'], (
        f'override member {override_uid} should have an individual cap'
    )
    assert abs(member_data['max_budget'] - expected_member_cap) <= 1e-6, (
        f'override member cap mismatch: expected {expected_member_cap}, '
        f'got {member_data["max_budget"]}'
    )

    # Default members: cap = baseline + default_user_monthly_limit.
    default_limit = settings.default_user_monthly_limit
    assert default_limit == USER_OVERRIDE  # override == default in this fixture
    for uid in adapter.user_ids[1:]:
        member_data = financial['members'][str(uid)]
        baseline = settings.user_cycle_start_spend[str(uid)]
        expected_member_cap = baseline + default_limit
        assert abs(member_data['max_budget'] - expected_member_cap) <= 1e-6, (
            f'default member {uid} cap mismatch: expected '
            f'{expected_member_cap}, got {member_data["max_budget"]}'
        )


# ---------------------------------------------------------------------------
# Acceptance criterion 4: Partial failures resume without renewing allowance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_failure_resumes_without_double_credit(
    upgrade_adapter: UpgradeBudgetTestAdapter,
) -> None:
    """A partial sync failure must converge on retry without double-crediting.

    Inject a fault on one ``/team/member_update`` call so the first
    maintenance run records an error.  Reset the fault and run maintenance
    again: caps must converge to the desired values and spend must not move
    (no inference happened — only reconciliation).
    """
    adapter = upgrade_adapter
    await _seed_over_cap_spend(adapter)

    # Establish a clean state first.
    await adapter.run_maintenance()
    pre_spend = (await adapter.financial_data())['team_spend']

    # Inject a fault on one member_update call.
    await adapter.fail_next_management_call('/team/member_update')
    await adapter.run_maintenance()
    degraded_settings = await adapter.get_settings()
    assert degraded_settings.litellm_last_sync_status == 'error', (
        f'expected error status after partial failure, got '
        f'{degraded_settings.litellm_last_sync_status}'
    )

    # Reset faults and retry.
    await adapter.reset_faults()
    await adapter.run_maintenance()
    repaired_settings = await adapter.get_settings()
    assert repaired_settings.litellm_last_sync_status == 'success', (
        f'expected success after retry, got '
        f'{repaired_settings.litellm_last_sync_status}'
    )

    # Spend must not have moved (no inference happened).
    post_spend = (await adapter.financial_data())['team_spend']
    assert post_spend == pre_spend, (
        f'spend moved during reconciliation: {pre_spend} -> {post_spend}'
    )

    # All caps must have converged to desired values.
    financial = await adapter.financial_data()
    expected_team = repaired_settings.cycle_start_spend + ORG_LIMIT
    assert abs(financial['team_max_budget'] - expected_team) <= 1e-6


# ---------------------------------------------------------------------------
# Acceptance criterion 5: Unresolved reconciliation fails the process/job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason='requires OHE-3254 (#356) fail-closed propagation in run_maintenance_tasks',
    strict=True,
)
async def test_unresolved_reconciliation_fails_job(
    upgrade_adapter: UpgradeBudgetTestAdapter,
) -> None:
    """When reconciliation cannot converge, the CronJob must exit nonzero.

    Depends on #356 (OHE-3254) which makes ``run_maintenance_tasks.main()``
    return ``False`` and the maintenance processor report ``success: False``
    when sync errors are detected.  On the current branch the runner swallows
    errors and marks tasks COMPLETED, so the job exits zero.
    """
    adapter = upgrade_adapter
    await _seed_over_cap_spend(adapter)

    # Inject a permanent fault on team updates so reconciliation can never
    # converge.
    await adapter.fail_next_management_call('/team/update', count=999)
    result = await adapter.run_maintenance()

    # #356 adds a 'status' field to the result; on this branch it is absent.
    assert result.get('status') == 'error', (
        f'expected status=error, got {result.get("status")}'
    )
    assert result.get('drift'), 'expected non-empty drift list'

    # The CronJob-level runner must propagate the failure.
    # This requires #356's run_tasks() -> bool and main() -> bool.
    import run_maintenance_tasks

    success = await run_maintenance_tasks.run_tasks()
    assert success is False, 'run_tasks should return False on reconciliation drift'
