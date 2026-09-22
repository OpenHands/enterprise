"""Real-service upgrade, ownership and maintenance failure contracts."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from storage.lite_llm_manager import LiteLlmManager
from storage.maintenance_task import MaintenanceTask, MaintenanceTaskStatus
from tests.integration.budgets.adapter import UpgradeBudgetTestAdapter

STALE_TEAM_CAP = 2.05264885
ORG_LIMIT = 1000.0
USER_OVERRIDE = 300.0
MEMBER_COUNT = 11


async def _seed_over_cap_spend(adapter: UpgradeBudgetTestAdapter) -> float:
    for _ in range(3):
        response = await adapter.send_request(adapter.user_ids[0])
        assert response.status_code == 200, response.text

    financial = await adapter.wait_for_spend(3.0)
    assert financial['team_spend'] == 3.0
    assert financial['team_spend'] > STALE_TEAM_CAP  # over-cap state
    return financial['team_spend']


@pytest.mark.asyncio
async def test_upgrade_assigns_unique_owned_keys(
    upgrade_adapter: UpgradeBudgetTestAdapter,
    async_session_maker,
    monkeypatch,
) -> None:
    adapter = upgrade_adapter
    await _seed_over_cap_spend(adapter)

    from server.maintenance_task_processor.managed_llm_key_ownership_processor import (
        ManagedLlmKeyOwnershipProcessor,
        ManagedLlmKeyOwnershipTarget,
    )

    targets = [
        ManagedLlmKeyOwnershipTarget(org_id=str(adapter.org_id), user_id=str(uid))
        for uid in adapter.all_user_ids
    ]
    processor = ManagedLlmKeyOwnershipProcessor(targets=targets)
    monkeypatch.setattr(
        'server.maintenance_task_processor.managed_llm_key_ownership_processor.a_session_maker',
        async_session_maker,
    )
    result = await processor(MaintenanceTask(processor_type='', processor_json='{}'))
    adapter.session.expire_all()
    assert result.get('error_count', 0) == 0, result

    members = await adapter.get_all_members()
    assert len(members) == MEMBER_COUNT

    keys: set[str] = set()
    for member in members:
        key = member.llm_api_key.get_secret_value()
        keys.add(key)
        adapter.keys[member.user_id] = key
        owned = await LiteLlmManager.verify_existing_key_strict(
            key,
            str(member.user_id),
            str(adapter.org_id),
            openhands_type=True,
        )
        assert owned, f'member {member.user_id} does not own their key'

    assert len(keys) == MEMBER_COUNT, 'keys are not unique across members'


@pytest.mark.asyncio
async def test_baseline_recovery_is_idempotent(
    upgrade_adapter: UpgradeBudgetTestAdapter,
) -> None:
    adapter = upgrade_adapter
    await _seed_over_cap_spend(adapter)

    await adapter.run_maintenance()
    first_settings = await adapter.get_settings()
    first_baselines: dict[str, float] = dict(
        first_settings.user_cycle_start_spend or {}
    )
    assert len(first_baselines) == MEMBER_COUNT, (
        f'expected {MEMBER_COUNT} baselines, got {len(first_baselines)}'
    )

    await adapter.run_maintenance()
    second_settings = await adapter.get_settings()
    second_baselines: dict[str, float] = dict(
        second_settings.user_cycle_start_spend or {}
    )
    assert second_baselines == first_baselines, (
        'baselines changed on second sync — recovery is not idempotent'
    )


@pytest.mark.asyncio
async def test_caps_match_after_readback(
    upgrade_adapter: UpgradeBudgetTestAdapter,
) -> None:
    adapter = upgrade_adapter
    await _seed_over_cap_spend(adapter)

    await adapter.run_maintenance()
    settings = await adapter.get_settings()
    financial = await adapter.financial_data()

    expected_team = settings.cycle_start_spend + settings.monthly_limit
    actual_team = financial['team_max_budget']
    assert actual_team is not None
    assert abs(actual_team - expected_team) <= 1e-6, (
        f'team cap mismatch: expected {expected_team}, got {actual_team}'
    )

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


@pytest.mark.asyncio
async def test_partial_failure_resumes_without_double_credit(
    upgrade_adapter: UpgradeBudgetTestAdapter,
) -> None:
    adapter = upgrade_adapter
    await _seed_over_cap_spend(adapter)

    await adapter.run_maintenance()
    pre_spend = (await adapter.financial_data())['team_spend']

    await adapter.fail_next_management_call('/team/member_update')
    await adapter.run_maintenance()
    degraded_settings = await adapter.get_settings()
    assert degraded_settings.litellm_last_sync_status == 'error', (
        f'expected error status after partial failure, got '
        f'{degraded_settings.litellm_last_sync_status}'
    )

    await adapter.reset_faults()
    await adapter.run_maintenance()
    repaired_settings = await adapter.get_settings()
    assert repaired_settings.litellm_last_sync_status == 'success', (
        f'expected success after retry, got '
        f'{repaired_settings.litellm_last_sync_status}'
    )

    post_spend = (await adapter.financial_data())['team_spend']
    assert post_spend == pre_spend, (
        f'spend moved during reconciliation: {pre_spend} -> {post_spend}'
    )

    financial = await adapter.financial_data()
    expected_team = repaired_settings.cycle_start_spend + ORG_LIMIT
    assert abs(financial['team_max_budget'] - expected_team) <= 1e-6


@pytest.mark.asyncio
async def test_unresolved_reconciliation_fails_job(
    upgrade_adapter: UpgradeBudgetTestAdapter,
    async_session_maker,
    test_database,
    monkeypatch,
) -> None:
    import run_maintenance_tasks
    from server.maintenance_task_processor.org_budget_maintenance_processor import (
        OrgBudgetMaintenanceProcessor,
    )

    adapter = upgrade_adapter
    await adapter.fail_next_management_call('/team/update', count=999)
    task = MaintenanceTask(status=MaintenanceTaskStatus.PENDING, delay=0)
    task.set_processor(OrgBudgetMaintenanceProcessor(org_ids=[str(adapter.org_id)]))
    adapter.session.add(task)
    await adapter.session.commit()
    monkeypatch.setattr(
        'server.maintenance_task_processor.org_budget_maintenance_processor.a_session_maker',
        async_session_maker,
    )
    engine = create_engine(test_database.sync_url)
    try:
        monkeypatch.setattr(
            run_maintenance_tasks, 'session_maker', sessionmaker(engine)
        )
        assert await run_maintenance_tasks.run_tasks() == 1
        await adapter.session.refresh(task)
        assert task.status == MaintenanceTaskStatus.ERROR
        assert task.info['error_count'] > 0
    finally:
        engine.dispose()
