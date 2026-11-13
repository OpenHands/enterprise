"""Worklogs: named, append-only logs scoped to a factory.

Factories live in the automation service (OpenHands/automation#725); a worklog
is keyed by the factory's id. Two doors reach the same data:

- ``/api/organizations/{org_id}/factories/{factory_id}/...`` for people,
  authorized by organization permission.
- ``/api/service/worklog/...`` for automation runs, authorized by the run token
  the automation service mints at dispatch. The token names the factory, so a
  run needs no ids in its URLs.

Every route needs the ``worklogs`` license feature. An entry body is opaque and
stored as plaintext: worklogs are narrative storage, so never put a secret in
an entry. The author of an entry is set by the server and cannot be supplied.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, field_validator

from server.auth.authorization import Permission, require_permission
from server.auth.worklog_token import WorklogRunClaims, require_worklog_run_token
from server.licensing.service import require_feature
from server.rate_limit import RateLimiter, create_redis_rate_limiter
from server.services import worklog_service
from server.services.worklog_service import (
    DEFAULT_ENTRIES_PAGE_SIZE,
    MAX_ENTRIES_PAGE_SIZE,
    MAX_NAME_LENGTH,
    MAX_SUMMARY_FACTORIES,
    FactoryLookupError,
    encode_cursor,
    existing_factory_ids,
    normalize_worklog_slug,
    parse_entry,
    parse_since,
    slugify_worklog_name,
)
from storage.worklog import Worklog, WorklogEntry
from storage.worklog_store import (
    EntryAuthor,
    WorklogConflictError,
    WorklogNotFoundError,
    WorklogStore,
    WorklogWithStats,
)

store = WorklogStore()

# Per (org, factory). None when the setting is empty or rate limiting is off.
write_rate_limiter: RateLimiter | None = create_redis_rate_limiter(
    worklog_service.WORKLOG_WRITE_RATE_LIMIT
)

FACTORY_PREFIX = '/api/organizations/{org_id}/factories/{factory_id}'

worklogs_router = APIRouter(prefix=FACTORY_PREFIX, tags=['Worklogs'])
worklog_summaries_router = APIRouter(
    prefix='/api/organizations/{org_id}', tags=['Worklogs']
)
worklog_run_router = APIRouter(prefix='/api/service/worklog', tags=['Worklogs'])


# --- Schemas ---


class WorklogCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str
    slug: str | None = None

    @field_validator('name')
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > MAX_NAME_LENGTH:
            raise ValueError(f'name must be 1 to {MAX_NAME_LENGTH} characters')
        return value

    @field_validator('slug')
    @classmethod
    def _slug(cls, value: str | None) -> str | None:
        return None if value is None else normalize_worklog_slug(value)


class WorklogUpdate(BaseModel):
    """Rename a worklog and/or make it the default. The slug never changes."""

    model_config = ConfigDict(extra='forbid')

    name: str | None = None
    is_default: bool | None = None

    @field_validator('name')
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or len(value) > MAX_NAME_LENGTH:
            raise ValueError(f'name must be 1 to {MAX_NAME_LENGTH} characters')
        return value

    @field_validator('is_default')
    @classmethod
    def _is_default(cls, value: bool | None) -> bool | None:
        if value is False:
            raise ValueError(
                'is_default cannot be set to false: make another worklog the '
                'default instead'
            )
        return value


class WorklogItem(BaseModel):
    id: UUID
    factory_id: UUID
    slug: str
    name: str
    is_default: bool
    created_at: datetime
    updated_at: datetime
    entry_count: int
    json_entry_count: int
    last_entry_at: datetime | None


class WorklogList(BaseModel):
    items: list[WorklogItem]


class EntryCreated(BaseModel):
    id: int
    created_at: datetime


class EntryAuthorItem(BaseModel):
    kind: str
    id: str | None
    name: str | None


class EntryItem(BaseModel):
    id: int
    created_at: datetime
    content_type: str
    title: str | None
    author: EntryAuthorItem
    run_id: UUID | None
    body: str


class EntryPage(BaseModel):
    items: list[EntryItem]
    # Position of the last entry returned: pass it as ``since`` to continue.
    # None when the page is empty.
    next_cursor: str | None
    has_more: bool


class FactorySummaryItem(BaseModel):
    factory_id: UUID
    worklog_count: int
    entry_count: int
    last_entry_at: datetime | None


class FactorySummaries(BaseModel):
    items: list[FactorySummaryItem]


def _worklog_item(item: WorklogWithStats) -> WorklogItem:
    worklog: Worklog = item.worklog
    return WorklogItem(
        id=worklog.id,
        factory_id=worklog.factory_id,
        slug=worklog.slug,
        name=worklog.name,
        is_default=worklog.is_default,
        created_at=worklog.created_at,
        updated_at=worklog.updated_at,
        entry_count=item.entry_count,
        json_entry_count=item.json_entry_count,
        last_entry_at=item.last_entry_at,
    )


def _entry_item(entry: WorklogEntry) -> EntryItem:
    return EntryItem(
        id=entry.id,
        created_at=entry.created_at,
        content_type=entry.content_type,
        title=entry.title,
        author=EntryAuthorItem(
            kind=entry.author_kind, id=entry.author_id, name=entry.author_name
        ),
        run_id=entry.run_id,
        body=entry.body,
    )


# --- Authorization ---


def _require_worklogs_license() -> None:
    require_feature('worklogs')()


def _user_access(permission: Permission):
    """Authenticate the person and check the permission, then the license."""
    checker = require_permission(permission)

    async def dependency(user_id: str = Depends(checker)) -> str:
        _require_worklogs_license()
        return user_id

    return dependency


async def _run_access(
    claims: WorklogRunClaims = Depends(require_worklog_run_token),
) -> WorklogRunClaims:
    _require_worklogs_license()
    return claims


# --- Shared behavior ---


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=what)


async def _ensure_ready(org_id: UUID, factory_id: UUID, *, confirm_factory: bool):
    """Create the factory's default worklog on first access.

    A person's first access first confirms the factory exists in the org, and
    fails closed: an unknown factory is 404 and an unreachable automation
    service is 503. A run token needs no check: the automation service signed
    it while the factory existed.
    """
    if await store.has_worklogs(org_id, factory_id):
        return
    if confirm_factory:
        try:
            known = await existing_factory_ids(org_id, [factory_id])
        except FactoryLookupError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail='Could not confirm the factory. Retry.',
            ) from exc
        if factory_id not in known:
            raise _not_found('Factory not found')
    await store.ensure_default(org_id, factory_id)


async def _list_worklogs(org_id: UUID, factory_id: UUID) -> WorklogList:
    return WorklogList(
        items=[
            _worklog_item(item)
            for item in await store.list_worklogs(org_id, factory_id)
        ]
    )


async def _create_worklog(
    org_id: UUID, factory_id: UUID, body: WorklogCreate
) -> WorklogItem:
    try:
        slug = body.slug or slugify_worklog_name(body.name)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    try:
        worklog = await store.create_worklog(org_id, factory_id, slug, body.name)
    except WorklogConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return _worklog_item(WorklogWithStats(worklog, 0, 0, None))


async def _update_worklog(
    org_id: UUID, factory_id: UUID, slug: str, body: WorklogUpdate
) -> WorklogItem:
    if body.name is None and body.is_default is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='Send name and/or is_default',
        )
    try:
        await store.update_worklog(
            org_id,
            factory_id,
            slug,
            name=body.name,
            make_default=body.is_default is True,
        )
        return _worklog_item(
            await store.get_worklog_with_stats(org_id, factory_id, slug)
        )
    except WorklogNotFoundError as exc:
        raise _not_found('Worklog not found') from exc
    except WorklogConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


async def _delete_worklog(org_id: UUID, factory_id: UUID, slug: str) -> None:
    try:
        await store.delete_worklog(org_id, factory_id, slug)
    except WorklogNotFoundError as exc:
        raise _not_found('Worklog not found') from exc
    except WorklogConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


async def _write_entry(
    request: Request,
    org_id: UUID,
    factory_id: UUID,
    worklog: str | None,
    author: EntryAuthor,
    run_id: UUID | None,
) -> EntryCreated:
    content_type = worklog_service.content_type_of(request)
    if write_rate_limiter is not None:
        await write_rate_limiter.hit('worklog_write', f'{org_id}:{factory_id}')
    raw = await worklog_service.read_limited_body(request)
    parsed = parse_entry(content_type, raw, request.headers.get('x-worklog-title'))
    try:
        entry_id, created_at = await store.add_entry(
            org_id,
            factory_id,
            worklog,
            content_type=parsed.content_type,
            title=parsed.title,
            body=parsed.body,
            author=author,
            run_id=run_id,
        )
    except WorklogNotFoundError as exc:
        raise _not_found('Worklog not found') from exc
    return EntryCreated(id=entry_id, created_at=created_at)


async def _read_entries(
    org_id: UUID,
    factory_id: UUID,
    worklog: str | None,
    since: str | None,
    limit: int,
    content_type: str | None,
) -> EntryPage:
    after = parse_since(since)
    try:
        rows = await store.list_entries(
            org_id,
            factory_id,
            worklog,
            after=after,
            limit=limit + 1,
            content_type=content_type,
        )
    except WorklogNotFoundError as exc:
        raise _not_found('Worklog not found') from exc
    page = rows[:limit]
    return EntryPage(
        items=[_entry_item(entry) for entry in page],
        next_cursor=(encode_cursor(page[-1].created_at, page[-1].id) if page else None),
        has_more=len(rows) > limit,
    )


def _user_author(user_id: str) -> EntryAuthor:
    return EntryAuthor(kind='user', id=user_id, name=None)


def _run_author(claims: WorklogRunClaims) -> EntryAuthor:
    return EntryAuthor(
        kind='automation',
        id=str(claims.automation_id),
        name=claims.automation_name,
    )


SinceParam = Annotated[
    str | None,
    Query(description='A cursor from a previous page, or an ISO 8601 timestamp'),
]
LimitParam = Annotated[
    int, Query(ge=1, le=MAX_ENTRIES_PAGE_SIZE, description='Entries per page')
]
ContentTypeParam = Annotated[
    str | None, Query(pattern='^(markdown|json)$', description='markdown or json')
]


# --- Routes for people ---


@worklogs_router.get('/worklogs')
async def list_worklogs(
    org_id: UUID,
    factory_id: UUID,
    _user: str = Depends(_user_access(Permission.VIEW_AUTOMATIONS)),
) -> WorklogList:
    """The factory's worklogs with entry counts, JSON entry counts and the time of
    the last entry. The first access creates the ``default`` worklog."""
    await _ensure_ready(org_id, factory_id, confirm_factory=True)
    return await _list_worklogs(org_id, factory_id)


@worklogs_router.post('/worklogs', status_code=status.HTTP_201_CREATED)
async def create_worklog(
    org_id: UUID,
    factory_id: UUID,
    body: WorklogCreate,
    _user: str = Depends(_user_access(Permission.MANAGE_AUTOMATIONS)),
) -> WorklogItem:
    await _ensure_ready(org_id, factory_id, confirm_factory=True)
    return await _create_worklog(org_id, factory_id, body)


@worklogs_router.patch('/worklogs/{worklog}')
async def update_worklog(
    org_id: UUID,
    factory_id: UUID,
    worklog: str,
    body: WorklogUpdate,
    _user: str = Depends(_user_access(Permission.MANAGE_AUTOMATIONS)),
) -> WorklogItem:
    """Rename a worklog, or make it the default (the swap is one transaction)."""
    await _ensure_ready(org_id, factory_id, confirm_factory=True)
    return await _update_worklog(org_id, factory_id, worklog, body)


@worklogs_router.delete('/worklogs/{worklog}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_worklog(
    org_id: UUID,
    factory_id: UUID,
    worklog: str,
    _user: str = Depends(_user_access(Permission.MANAGE_AUTOMATIONS)),
) -> None:
    """Delete a worklog and its entries. The default worklog cannot be deleted."""
    await _ensure_ready(org_id, factory_id, confirm_factory=True)
    await _delete_worklog(org_id, factory_id, worklog)


@worklogs_router.post(
    '/worklogs/{worklog}/entries', status_code=status.HTTP_201_CREATED
)
async def create_entry(
    request: Request,
    org_id: UUID,
    factory_id: UUID,
    worklog: str,
    user_id: str = Depends(_user_access(Permission.MANAGE_AUTOMATIONS)),
) -> EntryCreated:
    """Append an entry. The body is the raw request body, not a JSON envelope:
    ``Content-Type`` is ``text/markdown`` or ``application/json``. Send
    ``X-Worklog-Title`` to set the title."""
    await _ensure_ready(org_id, factory_id, confirm_factory=True)
    return await _write_entry(
        request, org_id, factory_id, worklog, _user_author(user_id), None
    )


@worklogs_router.get('/worklogs/{worklog}/entries')
async def list_entries(
    org_id: UUID,
    factory_id: UUID,
    worklog: str,
    since: SinceParam = None,
    limit: LimitParam = DEFAULT_ENTRIES_PAGE_SIZE,
    content_type: ContentTypeParam = None,
    _user: str = Depends(_user_access(Permission.VIEW_AUTOMATIONS)),
) -> EntryPage:
    await _ensure_ready(org_id, factory_id, confirm_factory=True)
    return await _read_entries(org_id, factory_id, worklog, since, limit, content_type)


@worklogs_router.post('/entries', status_code=status.HTTP_201_CREATED)
async def create_default_entry(
    request: Request,
    org_id: UUID,
    factory_id: UUID,
    user_id: str = Depends(_user_access(Permission.MANAGE_AUTOMATIONS)),
) -> EntryCreated:
    """Append an entry to the default worklog. See ``create_entry``."""
    await _ensure_ready(org_id, factory_id, confirm_factory=True)
    return await _write_entry(
        request, org_id, factory_id, None, _user_author(user_id), None
    )


@worklogs_router.get('/entries')
async def list_default_entries(
    org_id: UUID,
    factory_id: UUID,
    since: SinceParam = None,
    limit: LimitParam = DEFAULT_ENTRIES_PAGE_SIZE,
    content_type: ContentTypeParam = None,
    _user: str = Depends(_user_access(Permission.VIEW_AUTOMATIONS)),
) -> EntryPage:
    await _ensure_ready(org_id, factory_id, confirm_factory=True)
    return await _read_entries(org_id, factory_id, None, since, limit, content_type)


@worklog_summaries_router.get('/worklog-summaries')
async def worklog_summaries(
    org_id: UUID,
    factory_ids: Annotated[
        list[UUID],
        Query(alias='factory_id', min_length=1, max_length=MAX_SUMMARY_FACTORIES),
    ],
    _user: str = Depends(_user_access(Permission.VIEW_AUTOMATIONS)),
) -> FactorySummaries:
    """Totals per requested factory, for the factory cards. Reads existing rows
    only and creates no default worklog, so an unused factory reports zeros."""
    return FactorySummaries(
        items=[
            FactorySummaryItem(
                factory_id=s.factory_id,
                worklog_count=s.worklog_count,
                entry_count=s.entry_count,
                last_entry_at=s.last_entry_at,
            )
            for s in await store.summaries(org_id, factory_ids)
        ]
    )


# --- Routes for automation runs (run token) ---


@worklog_run_router.get('')
async def run_list_worklogs(
    claims: WorklogRunClaims = Depends(_run_access),
) -> WorklogList:
    """The run's own factory's worklogs."""
    await _ensure_ready(claims.org_id, claims.factory_id, confirm_factory=False)
    return await _list_worklogs(claims.org_id, claims.factory_id)


@worklog_run_router.post('/entries', status_code=status.HTTP_201_CREATED)
async def run_create_default_entry(
    request: Request, claims: WorklogRunClaims = Depends(_run_access)
) -> EntryCreated:
    """Append an entry to the default worklog, authored by the run's automation."""
    await _ensure_ready(claims.org_id, claims.factory_id, confirm_factory=False)
    return await _write_entry(
        request,
        claims.org_id,
        claims.factory_id,
        None,
        _run_author(claims),
        claims.run_id,
    )


@worklog_run_router.get('/entries')
async def run_list_default_entries(
    since: SinceParam = None,
    limit: LimitParam = DEFAULT_ENTRIES_PAGE_SIZE,
    content_type: ContentTypeParam = None,
    claims: WorklogRunClaims = Depends(_run_access),
) -> EntryPage:
    await _ensure_ready(claims.org_id, claims.factory_id, confirm_factory=False)
    return await _read_entries(
        claims.org_id, claims.factory_id, None, since, limit, content_type
    )


@worklog_run_router.post('/{worklog}/entries', status_code=status.HTTP_201_CREATED)
async def run_create_entry(
    request: Request,
    worklog: str,
    claims: WorklogRunClaims = Depends(_run_access),
) -> EntryCreated:
    await _ensure_ready(claims.org_id, claims.factory_id, confirm_factory=False)
    return await _write_entry(
        request,
        claims.org_id,
        claims.factory_id,
        worklog,
        _run_author(claims),
        claims.run_id,
    )


@worklog_run_router.get('/{worklog}/entries')
async def run_list_entries(
    worklog: str,
    since: SinceParam = None,
    limit: LimitParam = DEFAULT_ENTRIES_PAGE_SIZE,
    content_type: ContentTypeParam = None,
    claims: WorklogRunClaims = Depends(_run_access),
) -> EntryPage:
    await _ensure_ready(claims.org_id, claims.factory_id, confirm_factory=False)
    return await _read_entries(
        claims.org_id, claims.factory_id, worklog, since, limit, content_type
    )
