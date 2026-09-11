"""Read-only budget preflight used by the upgrade hooks.

Evaluates one organization's budget policy against the live LiteLLM state and
reports findings without mutating either side. ``_sync_litellm_budgets`` in
``org_budget_service`` remains the source of truth for the cap formulas; this
module mirrors them read-only so the pre-upgrade hook can run the new image
against an older schema and the post-upgrade gate can verify the readback.

Everything here is pure: callers load the rows and the LiteLLM snapshot.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from server.services.org_budget_service import (
    LiteLlmFinancialSnapshot,
    _budget_sync_readback_errors,
    _effective_user_budget_limit,
)
from storage.org_budget_settings import OrgBudgetSettings
from storage.org_user_budget_override import OrgUserBudgetOverride

ARTIFACT_TYPE = 'org_budget_preflight'
ARTIFACT_VERSION = 1

MODE_STRICT = 'strict'
MODE_ACKNOWLEDGE = 'acknowledge'
MODES = (MODE_STRICT, MODE_ACKNOWLEDGE)

PHASE_PRE = 'pre'
PHASE_POST = 'post'
PHASES = (PHASE_PRE, PHASE_POST)

SEVERITY_BLOCKING = 'blocking'
SEVERITY_INFO = 'info'

# Blocking finding codes.
LITELLM_UNREACHABLE = 'litellm_unreachable'
LAST_SYNC_ERROR = 'last_sync_error'
MAINTENANCE_FAILED = 'maintenance_failed'
MEMBER_MISSING_FROM_LITELLM = 'member_missing_from_litellm'
MEMBER_BASELINE_MISSING = 'member_baseline_missing'
CAP_DRIFT = 'cap_drift'
# Reserved for managed-key ownership verification (OHE-3252); never emitted yet.
KEY_OWNER_MISMATCH = 'key_owner_mismatch'

# Informational finding codes.
UNMAPPED_MEMBER = 'unmapped_member'
OVER_CAP_TEAM = 'over_cap_team'
OVER_CAP_MEMBER = 'over_cap_member'
SNAPSHOT_STALE = 'snapshot_stale'
SYNC_NEVER_RAN = 'sync_never_ran'
SCHEMA_MISSING_COLUMNS = 'schema_missing_columns'
SCHEMA_TABLE_MISSING = 'schema_table_missing'

_JSON_COLUMNS = (
    'user_cycle_start_spend',
    'litellm_last_member_spend',
    'litellm_known_member_ids',
)
_ERROR_DETAIL_MAX_CHARS = 500


def settings_from_row(row: Mapping[str, Any]) -> tuple[OrgBudgetSettings, list[str]]:
    """Build a transient settings object from whatever columns the schema has.

    The pre-upgrade hook runs the new image against the not-yet-migrated
    database, so columns added by later migrations may be absent. They are left
    ``None`` on the returned object (every helper tolerates that) and reported
    back as the second element.
    """
    columns = list(OrgBudgetSettings.__table__.columns.keys())
    present: dict[str, Any] = {name: row[name] for name in columns if name in row}
    missing = sorted(name for name in columns if name not in row)
    for name in _JSON_COLUMNS:
        value = present.get(name)
        if isinstance(value, str):
            present[name] = json.loads(value)
    return OrgBudgetSettings(**present), missing


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _age_seconds(value: datetime | None, now: datetime) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return max((now - value).total_seconds(), 0.0)


def _truncate(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:_ERROR_DETAIL_MAX_CHARS]


def evaluate_org(
    *,
    org_id: str,
    settings: OrgBudgetSettings,
    org_member_ids: Iterable[str],
    overrides: Iterable[OrgUserBudgetOverride],
    snapshot: LiteLlmFinancialSnapshot | None,
    snapshot_error: str | None = None,
    schema_missing_columns: Iterable[str] = (),
    maintenance_error: str | None = None,
    now: datetime | None = None,
    snapshot_max_age_seconds: float = 900.0,
) -> dict[str, Any]:
    """Evaluate one organization and return its sanitized report entry.

    Policy findings (missing members, missing baselines, cap drift) are
    blocking only while budgets are enabled; a disabled organization has
    nothing enforced, so the same observations are informational.
    """
    now = now or datetime.now(UTC)
    enforced = bool(settings.enabled)
    policy_severity = SEVERITY_BLOCKING if enforced else SEVERITY_INFO
    findings: list[dict[str, Any]] = []

    def add(code: str, severity: str, detail: str, **extra: Any) -> None:
        finding: dict[str, Any] = {'code': code, 'severity': severity, 'detail': detail}
        finding.update(extra)
        findings.append(finding)

    org_ids = {str(user_id) for user_id in org_member_ids}
    missing_columns = sorted(schema_missing_columns)
    if missing_columns:
        add(
            SCHEMA_MISSING_COLUMNS,
            SEVERITY_INFO,
            'org_budget_settings predates the current schema; listed columns are '
            'absent until migrations run',
            columns=missing_columns,
        )

    if maintenance_error is not None:
        add(MAINTENANCE_FAILED, SEVERITY_BLOCKING, _truncate(maintenance_error) or '')

    last_sync_status = settings.litellm_last_sync_status
    last_sync_error = _truncate(settings.litellm_last_sync_error)
    if last_sync_status == 'error':
        add(LAST_SYNC_ERROR, SEVERITY_BLOCKING, last_sync_error or 'unknown')
    elif last_sync_status is None and enforced:
        add(SYNC_NEVER_RAN, SEVERITY_INFO, 'budgets enabled but never synchronized')

    snapshot_age = _age_seconds(settings.litellm_last_spend_snapshot_at, now)
    if enforced and (snapshot_age is None or snapshot_age > snapshot_max_age_seconds):
        add(
            SNAPSHOT_STALE,
            SEVERITY_INFO,
            'no LiteLLM spend snapshot within the freshness window',
            age_seconds=snapshot_age,
        )

    baselines: dict[str, float] = dict(settings.user_cycle_start_spend or {})
    override_map = {str(override.user_id): override for override in overrides}
    default_limit = settings.default_user_monthly_limit

    desired_team_cap: float | None = None
    if enforced and settings.monthly_limit:
        desired_team_cap = (settings.cycle_start_spend or 0.0) + settings.monthly_limit

    members_missing_baseline: list[str] = []
    members_missing_from_litellm: list[str] = []
    unmapped_members: list[str] = []
    cap_drift: list[str] = []
    over_cap_members: list[str] = []
    over_cap_team = False
    desired_members: dict[str, float | None] = {}
    litellm_block: dict[str, Any]

    if snapshot is None:
        add(
            LITELLM_UNREACHABLE,
            SEVERITY_BLOCKING,
            _truncate(snapshot_error) or 'unknown',
        )
        litellm_block = {'status': 'unavailable', 'error': _truncate(snapshot_error)}
    else:
        litellm_ids = set(snapshot.members)
        members_missing_from_litellm = sorted(org_ids - litellm_ids)
        unmapped_members = sorted(litellm_ids - org_ids)
        governed = sorted(org_ids & litellm_ids)
        members_missing_baseline = [u for u in governed if u not in baselines]

        if members_missing_from_litellm:
            add(
                MEMBER_MISSING_FROM_LITELLM,
                policy_severity,
                'organization members absent from the LiteLLM team',
                user_ids=members_missing_from_litellm,
            )
        if unmapped_members:
            add(
                UNMAPPED_MEMBER,
                SEVERITY_INFO,
                'LiteLLM team members not mapped to organization users',
                user_ids=unmapped_members,
            )
        if members_missing_baseline:
            add(
                MEMBER_BASELINE_MISSING,
                policy_severity,
                'members without a cycle-start baseline; the next sync anchors '
                'them to live cumulative spend',
                user_ids=members_missing_baseline,
            )

        if enforced:
            for user_id in governed:
                if user_id in members_missing_baseline:
                    continue
                effective_limit, is_disabled, _ = _effective_user_budget_limit(
                    override_map.get(user_id), default_limit
                )
                if is_disabled or effective_limit is None:
                    desired_members[user_id] = None
                else:
                    desired_members[user_id] = baselines[user_id] + effective_limit
            cap_drift = _budget_sync_readback_errors(
                snapshot, desired_team_cap, desired_members
            )
            if cap_drift:
                add(
                    CAP_DRIFT,
                    SEVERITY_BLOCKING,
                    'LiteLLM caps differ from the desired policy',
                    details=cap_drift,
                )

        team_cap = snapshot.team_max_budget
        over_cap_team = team_cap is not None and snapshot.team_spend >= team_cap
        if over_cap_team:
            add(OVER_CAP_TEAM, SEVERITY_INFO, 'team spend has reached the team cap')
        over_cap_members = sorted(
            user_id
            for user_id, member in snapshot.members.items()
            if member.max_budget is not None
            and not member.uses_shared_budget
            and member.spend >= member.max_budget
        )
        if over_cap_members:
            add(
                OVER_CAP_MEMBER,
                SEVERITY_INFO,
                'members whose spend has reached their private cap',
                user_ids=over_cap_members,
            )

        litellm_block = {
            'status': 'live',
            'error': None,
            'observed_at': _isoformat(snapshot.observed_at),
            'team_spend': snapshot.team_spend,
            'team_max_budget': snapshot.team_max_budget,
            'members': {
                user_id: {
                    'spend': member.spend,
                    'max_budget': member.max_budget,
                    'uses_shared_budget': member.uses_shared_budget,
                }
                for user_id, member in sorted(snapshot.members.items())
            },
        }

    return {
        'org_id': org_id,
        'enabled': enforced,
        'monthly_limit': settings.monthly_limit,
        'default_user_monthly_limit': default_limit,
        'reset_day': settings.reset_day,
        'cycle_start_at': _isoformat(settings.cycle_start_at),
        'cycle_start_spend': settings.cycle_start_spend,
        'member_count': len(org_ids),
        'schema_missing_columns': missing_columns,
        'last_sync': {
            'at': _isoformat(settings.litellm_last_sync_at),
            'status': last_sync_status,
            'error': last_sync_error,
        },
        'snapshot_age_seconds': snapshot_age,
        'litellm': litellm_block,
        'desired': {'team_max_budget': desired_team_cap, 'members': desired_members},
        'members_missing_baseline': members_missing_baseline,
        'members_missing_from_litellm': members_missing_from_litellm,
        'unmapped_members': unmapped_members,
        'cap_drift': cap_drift,
        'over_cap': {'team': over_cap_team, 'members': over_cap_members},
        'key_owner_mismatches': [],
        'findings': findings,
        'blocking': any(f['severity'] == SEVERITY_BLOCKING for f in findings),
    }


def build_report(
    *,
    phase: str,
    mode: str,
    generated_at: datetime,
    schema_revision: str | None,
    orgs: list[dict[str, Any]],
    schema_table_missing: bool = False,
    error: str | None = None,
) -> dict[str, Any]:
    """Assemble the artifact printed by the preflight script."""
    finding_counts: Counter[str] = Counter()
    for org in orgs:
        finding_counts.update(f['code'] for f in org['findings'])
    if schema_table_missing:
        finding_counts[SCHEMA_TABLE_MISSING] += 1
    blocking_orgs = [org['org_id'] for org in orgs if org['blocking']]
    unreachable = [
        org['org_id'] for org in orgs if org['litellm'].get('status') == 'unavailable'
    ]
    return {
        'artifact_type': ARTIFACT_TYPE,
        'artifact_version': ARTIFACT_VERSION,
        'phase': phase,
        'mode': mode,
        'generated_at': _isoformat(generated_at),
        'schema_revision': schema_revision,
        'schema_table_missing': schema_table_missing,
        'litellm': {
            'reachable': not unreachable,
            'unreachable_orgs': unreachable,
        },
        'summary': {
            'orgs': len(orgs),
            'blocking_orgs': len(blocking_orgs),
            'blocking_org_ids': blocking_orgs,
            'blocking': bool(blocking_orgs) or error is not None,
            'finding_counts': dict(sorted(finding_counts.items())),
        },
        'orgs': orgs,
        'error': _truncate(error),
    }


def exit_code(mode: str, report: Mapping[str, Any]) -> int:
    """``acknowledge`` never blocks; ``strict`` fails on any blocking state."""
    if mode == MODE_ACKNOWLEDGE:
        return 0
    return 1 if report['summary']['blocking'] else 0
