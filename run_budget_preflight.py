"""Budget upgrade preflight and post-upgrade reconciliation gate.

Runs as a Helm hook Job from the enterprise image (see the openhands chart's
``budget-preflight-job.yaml`` and ``budget-reconcile-gate-job.yaml``). It
prints one JSON artifact line (``artifact_type: org_budget_preflight``) to
stdout and returns a shell exit code.

Phases (``BUDGET_PREFLIGHT_PHASE``):
  pre   Read-only. Runs the new image against the not-yet-migrated database and
        reports each enabled organization's budget state as found.
  post  Reconciles every enabled organization in-process, then evaluates the
        same report as a readback of what LiteLLM now enforces.

Modes (``BUDGET_PREFLIGHT_MODE``):
  acknowledge  Always exits 0; findings are an explicitly acknowledged
               availability-recovery (the default for the pre-upgrade hook).
  strict       Exits 1 when any organization has a blocking finding, LiteLLM is
               unreachable, or the run itself fails.

Like ``run_budget_maintenance`` this is a top-level module beside
``saas_server.py`` at the repository root (``/app`` in the Docker image).
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text

from run_budget_maintenance import _eligible_budget_org_ids
from server.logger import logger
from server.services.org_budget_preflight import (
    MODE_ACKNOWLEDGE,
    MODES,
    PHASE_POST,
    PHASE_PRE,
    PHASES,
    build_report,
    evaluate_org,
    exit_code,
    settings_from_row,
)
from server.services.org_budget_service import (
    LITELLM_FINANCIAL_READ_MAX_ATTEMPTS,
    LITELLM_FINANCIAL_READ_RETRY_DELAY_SECONDS,
    LiteLlmFinancialSnapshot,
    OrgBudgetService,
    _is_retryable_litellm_read_error,
    _parse_litellm_financial_snapshot,
)
from storage.database import a_session_maker, session_maker
from storage.lite_llm_manager import LiteLlmManager
from storage.org_member import OrgMember
from storage.org_user_budget_override import OrgUserBudgetOverride

DEFAULT_SNAPSHOT_MAX_AGE_SECONDS = 900.0
USAGE_EXIT_CODE = 2


def _read_env() -> tuple[str, str, float]:
    mode = os.environ.get('BUDGET_PREFLIGHT_MODE', MODE_ACKNOWLEDGE).strip().lower()
    phase = os.environ.get('BUDGET_PREFLIGHT_PHASE', PHASE_PRE).strip().lower()
    if mode not in MODES:
        raise ValueError(f'BUDGET_PREFLIGHT_MODE must be one of {MODES}, got {mode!r}')
    if phase not in PHASES:
        raise ValueError(
            f'BUDGET_PREFLIGHT_PHASE must be one of {PHASES}, got {phase!r}'
        )
    raw_age = os.environ.get('BUDGET_PREFLIGHT_SNAPSHOT_MAX_AGE_SECONDS', '').strip()
    max_age = float(raw_age) if raw_age else DEFAULT_SNAPSHOT_MAX_AGE_SECONDS
    return mode, phase, max_age


def _read_schema(session) -> tuple[str | None, bool]:
    """Return ``(alembic revision, org_budget_settings table exists)``."""
    try:
        revision = session.execute(
            text('SELECT version_num FROM alembic_version')
        ).scalar()
    except Exception as exc:
        session.rollback()
        logger.warning(
            'org_budget_preflight_schema_revision_unavailable',
            extra={'error': str(exc)},
        )
        revision = None
    table_present = bool(
        session.execute(
            text("SELECT to_regclass('org_budget_settings') IS NOT NULL")
        ).scalar()
    )
    return revision, table_present


def _load_org_context(session, org_id: str) -> dict[str, Any]:
    """Load the settings row, member ids, and overrides for one organization.

    The settings row is read with ``SELECT *`` so the pre-upgrade hook works on
    a schema that predates columns the ORM model knows about.
    """
    org_uuid = UUID(org_id)
    row = (
        session.execute(
            text('SELECT * FROM org_budget_settings WHERE org_id = :org_id'),
            {'org_id': org_uuid},
        )
        .mappings()
        .one()
    )
    settings, missing_columns = settings_from_row(row)
    member_ids = {
        str(user_id)
        for user_id in session.execute(
            select(OrgMember.user_id).where(OrgMember.org_id == org_uuid)
        ).scalars()
    }
    overrides = (
        session.execute(
            select(OrgUserBudgetOverride).where(
                OrgUserBudgetOverride.org_id == org_uuid
            )
        )
        .scalars()
        .all()
    )
    return {
        'settings': settings,
        'missing_columns': missing_columns,
        'member_ids': member_ids,
        'overrides': overrides,
    }


async def _fetch_snapshot(
    org_id: str,
) -> tuple[LiteLlmFinancialSnapshot | None, str | None]:
    """Read the live LiteLLM team state without touching the cached snapshot."""
    for attempt in range(1, LITELLM_FINANCIAL_READ_MAX_ATTEMPTS + 1):
        try:
            data = await LiteLlmManager.get_team_members_financial_data(org_id)
            return _parse_litellm_financial_snapshot(data), None
        except Exception as error:
            if (
                attempt == LITELLM_FINANCIAL_READ_MAX_ATTEMPTS
                or not _is_retryable_litellm_read_error(error)
            ):
                return None, str(error)
            await asyncio.sleep(LITELLM_FINANCIAL_READ_RETRY_DELAY_SECONDS * attempt)
    return None, 'unreachable'


async def _reconcile_orgs(org_ids: list[str]) -> dict[str, str]:
    """Run budget maintenance for each organization, committing per org.

    Mirrors ``OrgBudgetMaintenanceProcessor`` so the gate does not depend on
    the CronJob queue. Returns the organizations that failed to reconcile.
    """
    errors: dict[str, str] = {}
    async with a_session_maker() as session:
        service = OrgBudgetService(db_session=session)
        for org_id in org_ids:
            try:
                result = await service.run_budget_maintenance(UUID(org_id))
                await session.commit()
            except Exception as exc:
                await session.rollback()
                logger.exception(
                    'org_budget_preflight_reconcile_failed',
                    extra={'org_id': org_id},
                )
                errors[org_id] = str(exc)
                continue
            skipped = result.get('skipped')
            if skipped:
                errors[org_id] = f'skipped: {skipped}'
    return errors


async def _run(phase: str, mode: str, max_age: float) -> dict[str, Any]:
    generated_at = datetime.now(UTC)
    with session_maker() as session:
        schema_revision, table_present = _read_schema(session)
        org_ids = _eligible_budget_org_ids(session) if table_present else []

    maintenance_errors: dict[str, str] = {}
    if phase == PHASE_POST and org_ids:
        maintenance_errors = await _reconcile_orgs(org_ids)

    orgs: list[dict[str, Any]] = []
    for org_id in org_ids:
        with session_maker() as session:
            context = _load_org_context(session, org_id)
        snapshot, snapshot_error = await _fetch_snapshot(org_id)
        orgs.append(
            evaluate_org(
                org_id=org_id,
                settings=context['settings'],
                org_member_ids=context['member_ids'],
                overrides=context['overrides'],
                snapshot=snapshot,
                snapshot_error=snapshot_error,
                schema_missing_columns=context['missing_columns'],
                maintenance_error=maintenance_errors.get(org_id),
                now=generated_at,
                snapshot_max_age_seconds=max_age,
            )
        )

    return build_report(
        phase=phase,
        mode=mode,
        generated_at=generated_at,
        schema_revision=schema_revision,
        orgs=orgs,
        schema_table_missing=not table_present,
    )


def main() -> int:
    try:
        mode, phase, max_age = _read_env()
    except ValueError as exc:
        logger.error(
            'org_budget_preflight_invalid_configuration', extra={'error': str(exc)}
        )
        return USAGE_EXIT_CODE

    try:
        report = asyncio.run(_run(phase, mode, max_age))
    except Exception as exc:
        logger.exception('org_budget_preflight_failed')
        report = build_report(
            phase=phase,
            mode=mode,
            generated_at=datetime.now(UTC),
            schema_revision=None,
            orgs=[],
            error=str(exc),
        )

    print(json.dumps(report, sort_keys=True, default=str), flush=True)
    code = exit_code(mode, report)
    logger.info(
        'org_budget_preflight_completed',
        extra={
            'phase': phase,
            'mode': mode,
            'orgs': report['summary']['orgs'],
            'blocking_orgs': report['summary']['blocking_orgs'],
            'finding_counts': report['summary']['finding_counts'],
            'error': report['error'],
            'exit_code': code,
        },
    )
    return code


if __name__ == '__main__':
    raise SystemExit(main())
