"""Endpoint that accepts browser Content-Security-Policy violation reports.

Browsers send reports in two formats:

* Legacy: ``Content-Type: application/csp-report`` with body
  ``{"csp-report": {...}}``.
* Reporting API: ``Content-Type: application/reports+json`` with body
  ``[{"type": "csp-violation", "body": {...}, ...}, ...]``.

We accept both, log a single line per violation, and return 204.
"""

import logging
from typing import Any

from fastapi import APIRouter, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/security', tags=['Security'])


_LEGACY_CONTENT_TYPE = 'application/csp-report'
_REPORTING_CONTENT_TYPE = 'application/reports+json'


def _summarise(report: dict[str, Any]) -> str:
    """Return a short, log-safe summary of a single CSP report payload."""
    directive = report.get('violated-directive') or report.get('effective-directive')
    blocked = report.get('blocked-uri') or report.get('blockedURL')
    document = report.get('document-uri') or report.get('url')
    return f'directive={directive!r} blocked={blocked!r} document={document!r}'


def _extract_legacy(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    nested = payload.get('csp-report')
    return [nested] if isinstance(nested, dict) else []


def _extract_reporting(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    reports: list[dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        body = entry.get('body')
        if isinstance(body, dict):
            reports.append(body)
    return reports


@router.post('/csp-report', status_code=204)
async def report_csp_violation(request: Request) -> Response:
    """Receive a Content-Security-Policy violation report from the browser."""
    content_type = (
        (request.headers.get('content-type') or '').split(';', 1)[0].strip().lower()
    )

    try:
        payload = await request.json()
    except Exception:
        logger.warning('CSP report received with invalid JSON body')
        return Response(status_code=204)

    if content_type == _LEGACY_CONTENT_TYPE:
        reports = _extract_legacy(payload)
    elif content_type == _REPORTING_CONTENT_TYPE:
        reports = _extract_reporting(payload)
    else:
        # Tolerate browsers that omit the content-type.
        reports = _extract_legacy(payload) + _extract_reporting(payload)

    for report in reports:
        logger.info('CSP report: %s', _summarise(report))

    return Response(status_code=204)
