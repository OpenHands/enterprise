"""Tests for the budget upgrade preflight report and its script contract."""

from __future__ import annotations

import contextlib
import json
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from server.services.org_budget_preflight import (
    CAP_DRIFT,
    LAST_SYNC_ERROR,
    LITELLM_UNREACHABLE,
    MAINTENANCE_FAILED,
    MEMBER_BASELINE_MISSING,
    MEMBER_MISSING_FROM_LITELLM,
    MODE_ACKNOWLEDGE,
    MODE_STRICT,
    OVER_CAP_MEMBER,
    OVER_CAP_TEAM,
    SCHEMA_MISSING_COLUMNS,
    SEVERITY_BLOCKING,
    SEVERITY_INFO,
    SNAPSHOT_STALE,
    UNMAPPED_MEMBER,
    build_report,
    evaluate_org,
    exit_code,
    settings_from_row,
)
from server.services.org_budget_service import (
    LiteLlmFinancialSnapshot,
    LiteLlmMemberFinancialSnapshot,
)
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_user_budget_override import OrgUserBudgetOverride

# Import the script without touching a real database, mirroring
# tests/unit/test_run_maintenance_tasks.py.
mock_db = MagicMock()
with patch.dict(sys.modules, {'storage.database': mock_db}):
    import run_budget_preflight

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


def _settings(**overrides) -> OrgBudgetSettings:
    values = dict(
        org_id=uuid4(),
        enabled=True,
        monthly_limit=100.0,
        reset_day=1,
        default_user_monthly_limit=30.0,
        cycle_start_at=NOW - timedelta(days=3),
        cycle_start_spend=20.0,
        user_cycle_start_spend={},
        litellm_known_member_ids=[],
        litellm_last_sync_at=NOW - timedelta(minutes=5),
        litellm_last_sync_status='success',
        litellm_last_sync_error=None,
        litellm_last_spend_snapshot_at=NOW - timedelta(minutes=5),
        litellm_last_team_spend=20.0,
        litellm_last_member_spend={},
    )
    values.update(overrides)
    return OrgBudgetSettings(**values)


def _snapshot(
    *,
    team_spend: float = 20.0,
    team_max_budget: float | None = 120.0,
    members: dict[str, tuple[float, float | None, bool]] | None = None,
) -> LiteLlmFinancialSnapshot:
    return LiteLlmFinancialSnapshot(
        team_spend=team_spend,
        team_max_budget=team_max_budget,
        members={
            user_id: LiteLlmMemberFinancialSnapshot(
                spend=spend, max_budget=max_budget, uses_shared_budget=shared
            )
            for user_id, (spend, max_budget, shared) in (members or {}).items()
        },
        observed_at=NOW,
    )


def _evaluate(settings, member_ids, snapshot, **kwargs):
    return evaluate_org(
        org_id=str(settings.org_id),
        settings=settings,
        org_member_ids=member_ids,
        overrides=kwargs.pop('overrides', []),
        snapshot=snapshot,
        now=NOW,
        **kwargs,
    )


def _codes(entry: dict) -> dict[str, str]:
    return {finding['code']: finding['severity'] for finding in entry['findings']}


def test_legacy_member_without_baseline_is_blocking():
    user_id = str(uuid4())
    settings = _settings(user_cycle_start_spend={}, litellm_known_member_ids=[user_id])
    snapshot = _snapshot(members={user_id: (8.0, 5.0, False)})

    entry = _evaluate(settings, {user_id}, snapshot)

    assert _codes(entry)[MEMBER_BASELINE_MISSING] == SEVERITY_BLOCKING
    assert entry['members_missing_baseline'] == [user_id]
    assert entry['desired']['members'] == {}
    assert CAP_DRIFT not in _codes(entry)
    assert entry['blocking'] is True


def test_cap_drift_is_blocking_and_matching_caps_are_healthy():
    user_id = str(uuid4())
    settings = _settings(user_cycle_start_spend={user_id: 8.0})

    drifted = _evaluate(
        settings, {user_id}, _snapshot(members={user_id: (8.0, 5.0, False)})
    )
    assert _codes(drifted)[CAP_DRIFT] == SEVERITY_BLOCKING
    assert any('member_budget_mismatch' in detail for detail in drifted['cap_drift'])
    assert drifted['desired'] == {'team_max_budget': 120.0, 'members': {user_id: 38.0}}

    healthy = _evaluate(
        settings, {user_id}, _snapshot(members={user_id: (8.0, 38.0, False)})
    )
    assert healthy['cap_drift'] == []
    assert healthy['blocking'] is False


def test_override_and_disabled_member_desired_caps():
    limited = str(uuid4())
    disabled = str(uuid4())
    settings = _settings(user_cycle_start_spend={limited: 8.0, disabled: 4.0})
    overrides = [
        OrgUserBudgetOverride(user_id=limited, monthly_limit=50.0, is_disabled=False),
        OrgUserBudgetOverride(user_id=disabled, monthly_limit=None, is_disabled=True),
    ]
    snapshot = _snapshot(
        members={limited: (8.0, 58.0, False), disabled: (4.0, None, True)}
    )

    entry = _evaluate(settings, {limited, disabled}, snapshot, overrides=overrides)

    assert entry['desired']['members'] == {limited: 58.0, disabled: None}
    assert entry['blocking'] is False


def test_membership_findings_and_raw_caps_are_recorded():
    in_both = str(uuid4())
    only_in_org = str(uuid4())
    only_in_litellm = str(uuid4())
    settings = _settings(user_cycle_start_spend={in_both: 8.0})
    snapshot = _snapshot(
        members={in_both: (8.0, 38.0, False), only_in_litellm: (3.0, None, True)}
    )

    entry = _evaluate(settings, {in_both, only_in_org}, snapshot)

    codes = _codes(entry)
    assert codes[MEMBER_MISSING_FROM_LITELLM] == SEVERITY_BLOCKING
    assert codes[UNMAPPED_MEMBER] == SEVERITY_INFO
    assert entry['members_missing_from_litellm'] == [only_in_org]
    assert entry['unmapped_members'] == [only_in_litellm]
    assert entry['litellm']['members'][only_in_litellm] == {
        'spend': 3.0,
        'max_budget': None,
        'uses_shared_budget': True,
    }
    assert entry['blocking'] is True


def test_over_cap_is_informational_only():
    user_id = str(uuid4())
    settings = _settings(user_cycle_start_spend={user_id: 8.0})
    snapshot = _snapshot(
        team_spend=130.0, team_max_budget=120.0, members={user_id: (40.0, 38.0, False)}
    )

    entry = _evaluate(settings, {user_id}, snapshot)

    codes = _codes(entry)
    assert codes[OVER_CAP_MEMBER] == SEVERITY_INFO
    assert codes[OVER_CAP_TEAM] == SEVERITY_INFO
    assert entry['over_cap'] == {'team': True, 'members': [user_id]}
    assert entry['blocking'] is False


def test_litellm_unreachable_is_blocking():
    user_id = str(uuid4())
    entry = _evaluate(_settings(), {user_id}, None, snapshot_error='connect timeout')

    assert _codes(entry)[LITELLM_UNREACHABLE] == SEVERITY_BLOCKING
    assert entry['litellm'] == {'status': 'unavailable', 'error': 'connect timeout'}
    assert entry['members_missing_from_litellm'] == []
    assert entry['blocking'] is True


def test_disabled_org_reports_policy_findings_as_info():
    user_id = str(uuid4())
    settings = _settings(enabled=False, litellm_last_sync_status='skipped')

    entry = _evaluate(settings, {user_id}, _snapshot())

    assert _codes(entry)[MEMBER_MISSING_FROM_LITELLM] == SEVERITY_INFO
    assert entry['desired']['team_max_budget'] is None
    assert entry['blocking'] is False


def test_last_sync_error_and_missing_snapshot():
    settings = _settings(
        litellm_last_sync_status='error',
        litellm_last_sync_error='member_missing_from_litellm: x',
        litellm_last_spend_snapshot_at=None,
    )

    entry = _evaluate(settings, set(), _snapshot())

    codes = _codes(entry)
    assert codes[LAST_SYNC_ERROR] == SEVERITY_BLOCKING
    assert codes[SNAPSHOT_STALE] == SEVERITY_INFO
    assert entry['last_sync']['error'] == 'member_missing_from_litellm: x'
    assert entry['snapshot_age_seconds'] is None


def test_maintenance_error_is_blocking():
    entry = _evaluate(
        _settings(),
        set(),
        _snapshot(),
        maintenance_error='skipped: litellm_spend_unavailable',
    )

    assert _codes(entry)[MAINTENANCE_FAILED] == SEVERITY_BLOCKING
    assert entry['blocking'] is True


def test_settings_from_row_tolerates_an_older_schema():
    user_id = str(uuid4())
    row = {
        'id': 1,
        'org_id': uuid4(),
        'enabled': True,
        'monthly_limit': 100.0,
        'reset_day': 1,
        'default_user_monthly_limit': 30.0,
        'cycle_start_at': NOW - timedelta(days=3),
        'cycle_start_spend': 20.0,
        'litellm_last_sync_at': None,
        'litellm_last_sync_status': None,
        'litellm_last_sync_error': None,
        'created_at': NOW,
        'updated_at': NOW,
    }

    settings, missing = settings_from_row(row)

    assert 'user_cycle_start_spend' in missing
    assert 'litellm_known_member_ids' in missing
    assert settings.user_cycle_start_spend is None

    entry = _evaluate(
        settings,
        {user_id},
        _snapshot(members={user_id: (8.0, 5.0, False)}),
        schema_missing_columns=missing,
    )
    codes = _codes(entry)
    assert codes[MEMBER_BASELINE_MISSING] == SEVERITY_BLOCKING
    assert codes[SCHEMA_MISSING_COLUMNS] == SEVERITY_INFO
    assert entry['schema_missing_columns'] == missing


def test_settings_from_row_parses_json_columns_delivered_as_strings():
    settings, _ = settings_from_row(
        {'org_id': uuid4(), 'enabled': True, 'user_cycle_start_spend': '{"u": 1.5}'}
    )

    assert settings.user_cycle_start_spend == {'u': 1.5}


def test_build_report_summary_and_exit_codes():
    blocking_org = _evaluate(_settings(), {str(uuid4())}, None, snapshot_error='down')
    healthy_org = _evaluate(_settings(), set(), _snapshot())

    report = build_report(
        phase='pre',
        mode=MODE_STRICT,
        generated_at=NOW,
        schema_revision='160',
        orgs=[blocking_org, healthy_org],
    )

    assert report['summary']['orgs'] == 2
    assert report['summary']['blocking_orgs'] == 1
    assert report['summary']['blocking'] is True
    assert report['summary']['finding_counts'][LITELLM_UNREACHABLE] == 1
    assert report['litellm'] == {
        'reachable': False,
        'unreachable_orgs': [blocking_org['org_id']],
    }
    assert exit_code(MODE_STRICT, report) == 1
    assert exit_code(MODE_ACKNOWLEDGE, report) == 0

    clean = build_report(
        phase='post',
        mode=MODE_STRICT,
        generated_at=NOW,
        schema_revision='160',
        orgs=[healthy_org],
    )
    assert exit_code(MODE_STRICT, clean) == 0

    failed = build_report(
        phase='post',
        mode=MODE_STRICT,
        generated_at=NOW,
        schema_revision=None,
        orgs=[],
        error='boom',
    )
    assert failed['summary']['blocking'] is True
    assert exit_code(MODE_STRICT, failed) == 1


def _script_patches(monkeypatch, *, settings, member_ids, snapshot, reconcile=None):
    org_id = str(settings.org_id)
    monkeypatch.setattr(run_budget_preflight, 'session_maker', MagicMock())
    monkeypatch.setattr(
        run_budget_preflight, '_read_schema', lambda session: ('160', True)
    )
    monkeypatch.setattr(
        run_budget_preflight, '_eligible_budget_org_ids', lambda session: [org_id]
    )
    monkeypatch.setattr(
        run_budget_preflight,
        '_load_org_context',
        lambda session, _org_id: {
            'settings': settings,
            'missing_columns': [],
            'member_ids': member_ids,
            'overrides': [],
        },
    )
    monkeypatch.setattr(
        run_budget_preflight,
        '_fetch_snapshot',
        AsyncMock(return_value=(snapshot, None)),
    )
    reconcile_mock = AsyncMock(return_value=reconcile or {})
    monkeypatch.setattr(run_budget_preflight, '_reconcile_orgs', reconcile_mock)
    return org_id, reconcile_mock


def _artifact(capsys) -> dict:
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    artifact = json.loads(lines[-1])
    assert artifact['artifact_type'] == 'org_budget_preflight'
    return artifact


def test_main_strict_blocks_on_legacy_state_and_acknowledge_does_not(
    monkeypatch, capsys
):
    user_id = str(uuid4())
    settings = _settings(user_cycle_start_spend={}, litellm_known_member_ids=[user_id])
    org_id, reconcile_mock = _script_patches(
        monkeypatch,
        settings=settings,
        member_ids={user_id},
        snapshot=_snapshot(members={user_id: (8.0, 5.0, False)}),
    )
    monkeypatch.setenv('BUDGET_PREFLIGHT_PHASE', 'pre')

    monkeypatch.setenv('BUDGET_PREFLIGHT_MODE', 'strict')
    assert run_budget_preflight.main() == 1
    artifact = _artifact(capsys)
    assert artifact['phase'] == 'pre'
    assert artifact['schema_revision'] == '160'
    assert artifact['summary']['blocking_org_ids'] == [org_id]
    assert MEMBER_BASELINE_MISSING in artifact['summary']['finding_counts']
    reconcile_mock.assert_not_awaited()

    monkeypatch.setenv('BUDGET_PREFLIGHT_MODE', 'acknowledge')
    assert run_budget_preflight.main() == 0
    assert _artifact(capsys)['summary']['blocking'] is True


def test_main_post_phase_reconciles_before_evaluating(monkeypatch, capsys):
    user_id = str(uuid4())
    settings = _settings(user_cycle_start_spend={user_id: 8.0})
    org_id, reconcile_mock = _script_patches(
        monkeypatch,
        settings=settings,
        member_ids={user_id},
        snapshot=_snapshot(members={user_id: (8.0, 38.0, False)}),
        reconcile={str(settings.org_id): 'skipped: litellm_spend_unavailable'},
    )
    monkeypatch.setenv('BUDGET_PREFLIGHT_PHASE', 'post')
    monkeypatch.setenv('BUDGET_PREFLIGHT_MODE', 'strict')

    assert run_budget_preflight.main() == 1

    reconcile_mock.assert_awaited_once_with([org_id])
    artifact = _artifact(capsys)
    assert artifact['phase'] == 'post'
    assert artifact['summary']['finding_counts'] == {MAINTENANCE_FAILED: 1}


def test_main_invalid_mode_is_a_usage_error(monkeypatch):
    monkeypatch.setenv('BUDGET_PREFLIGHT_MODE', 'bogus')

    assert run_budget_preflight.main() == run_budget_preflight.USAGE_EXIT_CODE


def test_main_unhandled_error_fails_only_in_strict_mode(monkeypatch, capsys):
    monkeypatch.setattr(run_budget_preflight, 'session_maker', MagicMock())

    def explode(session):
        raise RuntimeError('database unavailable')

    monkeypatch.setattr(run_budget_preflight, '_read_schema', explode)

    monkeypatch.setenv('BUDGET_PREFLIGHT_MODE', 'strict')
    assert run_budget_preflight.main() == 1
    assert _artifact(capsys)['error'] == 'database unavailable'

    monkeypatch.setenv('BUDGET_PREFLIGHT_MODE', 'acknowledge')
    assert run_budget_preflight.main() == 0


@pytest.mark.asyncio
async def test_reconcile_orgs_commits_per_org_and_records_failures(monkeypatch):
    ok, skipped, failed = str(uuid4()), str(uuid4()), str(uuid4())
    session = AsyncMock()

    @contextlib.asynccontextmanager
    async def fake_session_maker():
        yield session

    service = MagicMock()
    service.run_budget_maintenance = AsyncMock(
        side_effect=[
            {'cycle_rolled': False},
            {'skipped': 'personal_org'},
            RuntimeError('boom'),
        ]
    )
    monkeypatch.setattr(run_budget_preflight, 'a_session_maker', fake_session_maker)
    monkeypatch.setattr(
        run_budget_preflight, 'OrgBudgetService', MagicMock(return_value=service)
    )

    errors = await run_budget_preflight._reconcile_orgs([ok, skipped, failed])

    assert errors == {skipped: 'skipped: personal_org', failed: 'boom'}
    assert session.commit.await_count == 2
    assert session.rollback.await_count == 1
