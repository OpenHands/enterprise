"""Worklog rules that are not database access: slugs, entry parsing and titles,
paging cursors, and the factory lookup in the automation service.

Entry bodies are opaque to the server. Markdown is stored verbatim and never
parsed. JSON is checked on write and stored as the received string, never
re-serialized. Worklogs are narrative storage: callers must not put secrets in
an entry.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence
from uuid import UUID

import httpx
from fastapi import HTTPException, Request, status

from openhands.app_server.utils.http_session import httpx_verify_option
from openhands.app_server.utils.logger import openhands_logger as logger
from server.auth.constants import AUTOMATION_SERVICE_URL
from storage.worklog_store import EntryCursor

WORKLOG_MAX_ENTRY_BYTES = int(os.environ.get('WORKLOG_MAX_ENTRY_BYTES', str(64 * 1024)))
# `limits`-style windows. An empty value disables the limit.
WORKLOG_WRITE_RATE_LIMIT = os.environ.get(
    'WORKLOG_WRITE_RATE_LIMIT', '10/second; 300/minute'
)

MARKDOWN = 'markdown'
JSON_CONTENT = 'json'
_CONTENT_TYPES = {'text/markdown': MARKDOWN, 'application/json': JSON_CONTENT}

MAX_SLUG_LENGTH = 63
MAX_NAME_LENGTH = 255
MAX_DERIVED_TITLE_LENGTH = 120
MAX_TITLE_LENGTH = 255
MAX_ENTRIES_PAGE_SIZE = 200
DEFAULT_ENTRIES_PAGE_SIZE = 50
MAX_SUMMARY_FACTORIES = 100
MAX_AUTHOR_NAME_LENGTH = 256

_SLUG_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
_NON_SLUG_RE = re.compile(r'[^a-z0-9]+')
_HEADING_RE = re.compile(r'^#{1,6}\s+(.*?)\s*$')

_LOOKUP_TIMEOUT_SECONDS = 5


def automations_service_key() -> str:
    """The key shared with the automation service (``AUTOMATIONS_SERVICE_KEY``)."""
    return os.environ.get('AUTOMATIONS_SERVICE_KEY', '').strip()


# --- Slugs ---


def slugify_worklog_name(name: str) -> str:
    """Derive a slug from a name. Raises ``ValueError`` when nothing is left."""
    slug = _NON_SLUG_RE.sub('-', name.strip().lower()).strip('-')
    slug = slug[:MAX_SLUG_LENGTH].rstrip('-')
    if not slug:
        raise ValueError(
            'The name has no letters or digits to derive a slug from; provide a slug'
        )
    return slug


def normalize_worklog_slug(slug: str) -> str:
    normalized = slug.strip().lower()
    if len(normalized) > MAX_SLUG_LENGTH:
        raise ValueError(f'slug must be at most {MAX_SLUG_LENGTH} characters')
    if not _SLUG_RE.fullmatch(normalized):
        raise ValueError(
            'slug may only contain lowercase letters, numbers, and single '
            'hyphens between them'
        )
    return normalized


# --- Entries ---


@dataclass(frozen=True)
class ParsedEntry:
    content_type: str
    title: str | None
    body: str


def content_type_of(request: Request) -> str:
    """``markdown`` or ``json`` from the request's ``Content-Type``, else 415."""
    header = request.headers.get('content-type', '')
    media_type = header.split(';', 1)[0].strip().lower()
    try:
        return _CONTENT_TYPES[media_type]
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail='Content-Type must be text/markdown or application/json',
        ) from None


async def read_limited_body(request: Request) -> bytes:
    """The raw request body, or 413 once it passes the entry size limit."""
    limit = WORKLOG_MAX_ENTRY_BYTES
    too_large = HTTPException(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        detail=f'An entry body may be at most {limit} bytes',
    )
    declared = request.headers.get('content-length')
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise too_large
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise too_large
    return bytes(body)


def derive_markdown_title(body: str) -> str | None:
    """The first non-empty line, without its heading marker, cut to 120 characters."""
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        heading = _HEADING_RE.match(line)
        title = heading.group(1) if heading else line
        return title[:MAX_DERIVED_TITLE_LENGTH] or None
    return None


def parse_entry(content_type: str, raw: bytes, title_header: str | None) -> ParsedEntry:
    """Validate a body and pick its title. Rejects with 422 when it is not valid
    UTF-8 or, for JSON, not valid JSON."""
    try:
        body = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f'The body is not valid UTF-8 (byte {exc.start})',
        ) from exc
    if '\x00' in body:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='The body contains a NUL character',
        )
    if content_type == JSON_CONTENT:
        _check_json(body)
    override = title_header.strip()[:MAX_TITLE_LENGTH] if title_header else ''
    title: str | None
    if override:
        title = override
    elif content_type == MARKDOWN:
        title = derive_markdown_title(body)
    else:
        title = None
    return ParsedEntry(content_type=content_type, title=title, body=body)


def _reject_constant(name: str) -> None:
    raise ValueError(f'{name} is not valid JSON')


def _check_json(body: str) -> None:
    try:
        json.loads(body, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                'message': f'The body is not valid JSON: {exc.msg}',
                'line': exc.lineno,
                'column': exc.colno,
                'position': exc.pos,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f'The body is not valid JSON: {exc}',
        ) from exc


# --- Paging ---


def encode_cursor(created_at: datetime, entry_id: int) -> str:
    raw = f'{created_at.isoformat()}|{entry_id}'
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip('=')


def parse_since(since: str | None) -> EntryCursor | datetime | None:
    """A cursor a previous page returned, or an ISO 8601 timestamp. 422 otherwise."""
    if not since:
        return None
    try:
        raw = base64.urlsafe_b64decode(since + '=' * (-len(since) % 4)).decode()
        created_at_text, entry_id_text = raw.split('|')
        return EntryCursor(datetime.fromisoformat(created_at_text), int(entry_id_text))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(since)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='since must be a cursor from a previous page or an ISO 8601 timestamp',
        ) from None
    if parsed.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='since must include a UTC offset',
        )
    return parsed


# --- Factory lookup in the automation service ---


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        verify=httpx_verify_option(), timeout=_LOOKUP_TIMEOUT_SECONDS
    )


class FactoryLookupError(Exception):
    """The automation service could not say which factories exist."""


async def existing_factory_ids(org_id: UUID, factory_ids: Sequence[UUID]) -> set[UUID]:
    """The ids among ``factory_ids`` that exist in ``org_id``, asked of the
    automation service with the service key, at most 100 per request.

    Fails closed: not configured, unreachable, or an unexpected answer raises
    ``FactoryLookupError``, and nothing is guessed.
    """
    service_key = automations_service_key()
    if not AUTOMATION_SERVICE_URL or not service_key:
        raise FactoryLookupError('The automation service is not configured')
    url = f'{AUTOMATION_SERVICE_URL.rstrip("/")}/v1/service/factories'
    found: set[UUID] = set()
    try:
        async with _http_client() as client:
            for start in range(0, len(factory_ids), 100):
                batch = factory_ids[start : start + 100]
                response = await client.get(
                    url,
                    params=[
                        ('org_id', str(org_id)),
                        *(('id', str(factory_id)) for factory_id in batch),
                    ],
                    headers={'X-Service-API-Key': service_key},
                )
                response.raise_for_status()
                for item in response.json()['factories']:
                    if UUID(item['org_id']) == org_id:
                        found.add(UUID(item['id']))
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning(
            'worklog:factory_lookup_failed',
            extra={'org_id': str(org_id), 'error': type(exc).__name__},
        )
        raise FactoryLookupError('Could not reach the automation service') from exc
    return found
