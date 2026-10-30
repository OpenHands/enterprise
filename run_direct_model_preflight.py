"""Pre-upgrade warning for LLM settings that relied on sandbox provider credentials.

Runs as a Helm pre-upgrade hook Job from the enterprise image (see the openhands
chart's ``direct-model-preflight-job.yaml``). Read-only and report-only: it
prints a human-readable warning, then one JSON artifact line
(``artifact_type: direct_model_preflight``), and always exits 0 so it can never
block an upgrade. Like ``run_budget_preflight`` it runs the new image against the
not-yet-migrated database, so it reads only the columns it needs.

Top-level module beside ``saas_server.py`` (``/app`` in the Docker image).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from server.logger import logger
from server.services.direct_model_preflight import (
    build_report,
    find_direct_model_configs,
    render_summary,
)
from storage.database import session_maker
from storage.encrypt_utils import decrypt_value

# (label, id SQL expression, table, column, whether the column is encrypted)
SOURCES = (
    ('org.agent_settings', 'id', 'org', 'agent_settings', False),
    ('org.llm_profiles', 'id', 'org', 'llm_profiles', True),
    ('user.llm_profiles', 'id', '"user"', 'llm_profiles', True),
    (
        'org_member.agent_settings_diff',
        "CAST(org_id AS TEXT) || '/' || CAST(user_id AS TEXT)",
        'org_member',
        'agent_settings_diff',
        False,
    ),
    (
        'user_settings.agent_settings',
        'keycloak_user_id',
        'user_settings',
        'agent_settings',
        False,
    ),
)


def _load_blob(value: Any, encrypted: bool) -> Any:
    if encrypted:
        return json.loads(decrypt_value(value))
    return json.loads(value) if isinstance(value, str) else value


def _scan_source(
    session, label: str, id_sql: str, table: str, column: str, encrypted: bool
) -> list[dict[str, str]]:
    rows = session.execute(
        text(
            f'SELECT {id_sql} AS row_id, {column} AS blob FROM {table} '
            f'WHERE {column} IS NOT NULL'
        )
    ).mappings()
    findings = []
    for row in rows:
        blob = _load_blob(row['blob'], encrypted)
        findings.extend(find_direct_model_configs(label, str(row['row_id']), blob))
    return findings


def scan() -> tuple[list[dict[str, str]], dict[str, str]]:
    """Return ``(findings, skipped_sources)``; a source that cannot be read is skipped."""
    findings: list[dict[str, str]] = []
    skipped: dict[str, str] = {}
    for label, id_sql, table, column, encrypted in SOURCES:
        with session_maker() as session:
            try:
                findings.extend(
                    _scan_source(session, label, id_sql, table, column, encrypted)
                )
            except Exception as exc:
                session.rollback()
                skipped[label] = f'{type(exc).__name__}: {exc}'[:300]
                logger.warning(
                    'direct_model_preflight_source_skipped',
                    extra={'source': label, 'error': skipped[label]},
                )
    return findings, skipped


def main() -> int:
    generated_at = datetime.now(UTC).isoformat()
    try:
        findings, skipped = scan()
        report = build_report(
            generated_at=generated_at, findings=findings, skipped_sources=skipped
        )
    except Exception as exc:
        logger.exception('direct_model_preflight_failed')
        report = build_report(
            generated_at=generated_at,
            findings=[],
            skipped_sources={},
            error=f'{type(exc).__name__}: {exc}',
        )

    print(render_summary(report), flush=True)
    print(json.dumps(report, sort_keys=True), flush=True)
    logger.info(
        'direct_model_preflight_completed',
        extra={
            'affected_configs': report['summary']['affected_configs'],
            'skipped_sources': sorted(report['skipped_sources']),
            'error': report['error'],
        },
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
