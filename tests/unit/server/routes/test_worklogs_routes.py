"""Worklog routes against a real PostgreSQL database.

Both doors are covered: organization members (``/api/organizations/...``) and
automation runs (``/api/service/worklog``, run token). The automation service is
the only thing faked: ``FactoryDirectory`` answers the factory lookup over a
mock HTTP transport, so the real client code runs.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import jwt
import limits
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from openhands.app_server.user_auth import get_user_id
from server.auth.worklog_token import derive_worklog_signing_key
from server.licensing import service as license_module
from server.licensing.config import LicenseConfig
from server.licensing.service import LicenseService
from server.rate_limit import RateLimiter, setup_rate_limit_handler
from server.routes.worklogs import (
    worklog_run_router,
    worklog_summaries_router,
    worklogs_router,
)
from storage.worklog import Worklog, WorklogEntry

SERVICE_KEY = 'worklog-routes-service-key'
AUTOMATION_URL = 'https://automation.test'
USER_ID = str(uuid.uuid4())
AUTOMATION_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()
FENCED_MARKDOWN = (
    '## Plan\r\n\r\n```json\r\n{"workers": [1, 2]}\r\n```\r\nnaïve – 日本語 🚀\r\n'
)


class FactoryDirectory:
    """The automation service's ``GET /v1/service/factories``."""

    def __init__(self) -> None:
        self.factories: dict[uuid.UUID, uuid.UUID] = {}
        self.status = 200
        self.requests: list[httpx.Request] = []

    def add(self, org_id: uuid.UUID) -> uuid.UUID:
        factory_id = uuid.uuid4()
        self.factories[factory_id] = org_id
        return factory_id

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status)
        org_id = uuid.UUID(request.url.params['org_id'])
        wanted = [uuid.UUID(v) for v in request.url.params.get_list('id')]
        return httpx.Response(
            200,
            json={
                'factories': [
                    {'id': str(f), 'org_id': str(org_id)}
                    for f in wanted
                    if self.factories.get(f) == org_id
                ]
            },
        )


@pytest.fixture
def directory(monkeypatch) -> FactoryDirectory:
    directory = FactoryDirectory()
    monkeypatch.setenv('AUTOMATIONS_SERVICE_KEY', SERVICE_KEY)
    with (
        patch('server.services.worklog_service.AUTOMATION_SERVICE_URL', AUTOMATION_URL),
        patch(
            'server.services.worklog_service._http_client',
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(directory.respond)),
        ),
    ):
        yield directory


@pytest.fixture(autouse=True)
def database(async_session_maker):
    with patch('storage.worklog_store.a_session_maker', async_session_maker):
        yield


@pytest.fixture(autouse=True)
def license_enforcement_off(monkeypatch):
    install_license(monkeypatch, enforcement_enabled=False)


@pytest.fixture(autouse=True)
def no_rate_limit():
    with patch('server.routes.worklogs.write_rate_limiter', None):
        yield


def install_license(monkeypatch, **config) -> LicenseService:
    values = {
        'enforcement_enabled': True,
        'license_key': None,
        'license_server_url': 'https://cc.example',
        'check_interval_seconds': 1800,
    }
    values.update(config)
    service = LicenseService(LicenseConfig(**values))
    monkeypatch.setattr(license_module, '_license_service', service)
    return service


@pytest.fixture
def org(create_org):
    return create_org()


@pytest.fixture
def role():
    """The caller's role name in the org, changeable per test. None: not a member."""
    state = SimpleNamespace(name='admin')

    async def get_role(user_id, org_id):
        return SimpleNamespace(name=state.name) if state.name else None

    with patch(
        'server.auth.authorization.get_user_org_role', AsyncMock(side_effect=get_role)
    ):
        yield state


@pytest.fixture
def app(role) -> FastAPI:
    app = FastAPI()
    app.include_router(worklogs_router)
    app.include_router(worklog_summaries_router)
    app.include_router(worklog_run_router)
    setup_rate_limit_handler(app)
    app.dependency_overrides[get_user_id] = lambda: USER_ID
    return app


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as client:
        yield client


@pytest.fixture
def factory(org, directory) -> uuid.UUID:
    return directory.add(org.id)


def base(org, factory) -> str:
    return f'/api/organizations/{org.id}/factories/{factory}'


def mint(org, factory, key: str = SERVICE_KEY, **overrides) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        'aud': 'worklog',
        'org_id': str(org.id),
        'factory_id': str(factory),
        'automation_id': str(AUTOMATION_ID),
        'automation_name': 'Orchestrator',
        'run_id': str(RUN_ID),
        'iat': int(now.timestamp()),
        'exp': int((now + timedelta(hours=24)).timestamp()),
    }
    claims.update(overrides)
    return jwt.encode(claims, derive_worklog_signing_key(key), algorithm='HS256')


def run_headers(org, factory, **overrides) -> dict[str, str]:
    return {'Authorization': f'Bearer {mint(org, factory, **overrides)}'}


async def post_entry(client, url, body, content_type='text/markdown', **headers):
    return await client.post(
        url,
        content=body.encode() if isinstance(body, str) else body,
        headers={'Content-Type': content_type, **headers},
    )


async def count(async_session_maker, model, *where) -> int:
    async with async_session_maker() as session:
        return await session.scalar(
            select(func.count()).select_from(model).where(*where)
        )


# --- Default worklog and the factory check ---


async def test_first_access_creates_the_default_worklog(client, org, factory):
    response = await client.get(f'{base(org, factory)}/worklogs')

    assert response.status_code == 200, response.text
    [item] = response.json()['items']
    assert item['slug'] == 'default'
    assert item['name'] == 'Default'
    assert item['is_default'] is True
    assert item['factory_id'] == str(factory)
    assert (item['entry_count'], item['json_entry_count']) == (0, 0)
    assert item['last_entry_at'] is None


async def test_concurrent_first_requests_create_one_default(
    client, org, factory, async_session_maker
):
    responses = await asyncio.gather(
        *(client.get(f'{base(org, factory)}/worklogs') for _ in range(10))
    )

    assert {r.status_code for r in responses} == {200}
    assert await count(async_session_maker, Worklog, Worklog.factory_id == factory) == 1
    assert await count(async_session_maker, Worklog, Worklog.is_default) == 1


async def test_an_unknown_factory_is_404_and_creates_nothing(
    client, org, directory, async_session_maker
):
    response = await client.get(f'{base(org, uuid.uuid4())}/worklogs')

    assert response.status_code == 404
    assert await count(async_session_maker, Worklog) == 0


async def test_a_factory_of_another_org_is_404(
    client, org, create_org, directory, async_session_maker
):
    other_factory = directory.add(create_org().id)

    response = await client.get(f'{base(org, other_factory)}/worklogs')

    assert response.status_code == 404
    assert await count(async_session_maker, Worklog) == 0


@pytest.mark.parametrize('status', [401, 500, 503])
async def test_a_failing_lookup_is_503_and_creates_nothing(
    client, org, factory, directory, async_session_maker, status
):
    directory.status = status

    response = await client.get(f'{base(org, factory)}/worklogs')

    assert response.status_code == 503
    assert await count(async_session_maker, Worklog) == 0


async def test_an_unconfigured_automation_service_is_503(
    client, org, factory, monkeypatch
):
    monkeypatch.delenv('AUTOMATIONS_SERVICE_KEY')

    response = await client.get(f'{base(org, factory)}/worklogs')

    assert response.status_code == 503


async def test_the_lookup_sends_the_org_factory_and_service_key_once(
    client, org, factory, directory
):
    for _ in range(3):
        assert (await client.get(f'{base(org, factory)}/worklogs')).status_code == 200

    [request] = directory.requests
    assert request.url.path == '/v1/service/factories'
    assert request.url.params['org_id'] == str(org.id)
    assert request.url.params.get_list('id') == [str(factory)]
    assert request.headers['X-Service-API-Key'] == SERVICE_KEY


# --- Managing worklogs ---


async def test_create_derives_the_slug_from_the_name(client, org, factory):
    response = await client.post(
        f'{base(org, factory)}/worklogs', json={'name': 'Release Notes'}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body['slug'], body['name'], body['is_default']) == (
        'release-notes',
        'Release Notes',
        False,
    )
    assert (body['entry_count'], body['json_entry_count']) == (0, 0)


async def test_create_accepts_a_slug_and_lowercases_it(client, org, factory):
    response = await client.post(
        f'{base(org, factory)}/worklogs', json={'name': 'A', 'slug': 'Notes-1'}
    )

    assert response.status_code == 201
    assert response.json()['slug'] == 'notes-1'


@pytest.mark.parametrize(
    'body',
    [
        {'name': ''},
        {'name': '   '},
        {'name': 'x' * 256},
        {'name': '!!!'},
        {'name': 'ok', 'slug': 'Bad_Slug'},
        {'name': 'ok', 'is_default': True},
        {},
    ],
)
async def test_create_rejects_bad_input(client, org, factory, body):
    response = await client.post(f'{base(org, factory)}/worklogs', json=body)

    assert response.status_code == 422, response.text


async def test_slug_and_name_are_unique_within_a_factory(client, org, factory):
    url = f'{base(org, factory)}/worklogs'
    await client.post(url, json={'name': 'Notes'})

    same_name = await client.post(url, json={'name': 'Notes', 'slug': 'other'})
    same_slug = await client.post(url, json={'name': 'Other name', 'slug': 'notes'})
    default_slug = await client.post(url, json={'name': 'Mine', 'slug': 'default'})

    assert (same_name.status_code, same_slug.status_code) == (409, 409)
    assert default_slug.status_code == 409


async def test_the_same_slug_may_exist_in_another_factory(
    client, org, factory, directory
):
    other = directory.add(org.id)

    first = await client.post(f'{base(org, factory)}/worklogs', json={'name': 'Notes'})
    second = await client.post(f'{base(org, other)}/worklogs', json={'name': 'Notes'})

    assert (first.status_code, second.status_code) == (201, 201)


async def test_rename_changes_only_the_name(client, org, factory):
    await client.post(f'{base(org, factory)}/worklogs', json={'name': 'Notes'})

    response = await client.patch(
        f'{base(org, factory)}/worklogs/notes', json={'name': 'Field notes'}
    )

    assert response.status_code == 200, response.text
    assert (response.json()['slug'], response.json()['name']) == (
        'notes',
        'Field notes',
    )


async def test_the_slug_cannot_be_changed(client, org, factory):
    await client.post(f'{base(org, factory)}/worklogs', json={'name': 'Notes'})

    response = await client.patch(
        f'{base(org, factory)}/worklogs/notes', json={'slug': 'other'}
    )

    assert response.status_code == 422


async def test_rename_to_a_taken_name_is_409(client, org, factory):
    await client.post(f'{base(org, factory)}/worklogs', json={'name': 'Notes'})

    response = await client.patch(
        f'{base(org, factory)}/worklogs/notes', json={'name': 'Default'}
    )

    assert response.status_code == 409


@pytest.mark.parametrize('body', [{}, {'is_default': False}, {'name': ''}])
async def test_update_rejects_bad_input(client, org, factory, body):
    await client.post(f'{base(org, factory)}/worklogs', json={'name': 'Notes'})

    response = await client.patch(f'{base(org, factory)}/worklogs/notes', json=body)

    assert response.status_code == 422


async def test_update_of_an_unknown_worklog_is_404(client, org, factory):
    response = await client.patch(
        f'{base(org, factory)}/worklogs/missing', json={'name': 'x'}
    )

    assert response.status_code == 404


async def test_renaming_the_default_does_not_change_which_is_default(
    client, org, factory
):
    await client.get(f'{base(org, factory)}/worklogs')

    response = await client.patch(
        f'{base(org, factory)}/worklogs/default', json={'name': 'Main'}
    )

    assert response.status_code == 200
    assert (response.json()['name'], response.json()['is_default']) == ('Main', True)


async def test_making_another_worklog_the_default_swaps_it(
    client, org, factory, async_session_maker
):
    await client.post(f'{base(org, factory)}/worklogs', json={'name': 'Notes'})

    response = await client.patch(
        f'{base(org, factory)}/worklogs/notes', json={'is_default': True}
    )

    assert response.status_code == 200
    assert response.json()['is_default'] is True
    listing = (await client.get(f'{base(org, factory)}/worklogs')).json()['items']
    assert {i['slug']: i['is_default'] for i in listing} == {
        'notes': True,
        'default': False,
    }
    assert await count(async_session_maker, Worklog, Worklog.is_default) == 1
    await post_entry(client, f'{base(org, factory)}/entries', 'goes to the new default')
    notes = await client.get(f'{base(org, factory)}/worklogs/notes/entries')
    assert [e['body'] for e in notes.json()['items']] == ['goes to the new default']


async def test_the_default_worklog_cannot_be_deleted(client, org, factory):
    await client.get(f'{base(org, factory)}/worklogs')

    response = await client.delete(f'{base(org, factory)}/worklogs/default')

    assert response.status_code == 409


async def test_deleting_a_worklog_removes_its_entries(
    client, org, factory, async_session_maker
):
    await client.post(f'{base(org, factory)}/worklogs', json={'name': 'Notes'})
    await post_entry(client, f'{base(org, factory)}/worklogs/notes/entries', 'one')

    response = await client.delete(f'{base(org, factory)}/worklogs/notes')

    assert response.status_code == 204
    assert await count(async_session_maker, WorklogEntry) == 0
    gone = await client.get(f'{base(org, factory)}/worklogs/notes/entries')
    assert gone.status_code == 404


async def test_deleting_an_unknown_worklog_is_404(client, org, factory):
    assert (
        await client.delete(f'{base(org, factory)}/worklogs/missing')
    ).status_code == 404


async def test_worklog_list_counts_entries_json_entries_and_last_entry(
    client, org, factory
):
    url = f'{base(org, factory)}/entries'
    await post_entry(client, url, '## one')
    await post_entry(client, url, '{"a": 1}', 'application/json')
    last = await post_entry(client, url, '{"b": 2}', 'application/json')

    [item] = (await client.get(f'{base(org, factory)}/worklogs')).json()['items']

    assert (item['entry_count'], item['json_entry_count']) == (3, 2)
    assert item['last_entry_at'] == last.json()['created_at']


# --- Writing and reading entries ---


async def test_markdown_with_fenced_json_reads_back_byte_identical(
    client, org, factory
):
    created = await post_entry(client, f'{base(org, factory)}/entries', FENCED_MARKDOWN)

    assert created.status_code == 201, created.text
    assert set(created.json()) == {'id', 'created_at'}
    [entry] = (await client.get(f'{base(org, factory)}/entries')).json()['items']
    assert entry['body'] == FENCED_MARKDOWN
    assert entry['content_type'] == 'markdown'
    assert entry['id'] == created.json()['id']


async def test_valid_json_reads_back_as_the_same_string(client, org, factory):
    body = '{ "b":1,  "a" : [1, 2,3] }\n'

    created = await post_entry(
        client, f'{base(org, factory)}/entries', body, 'application/json'
    )

    assert created.status_code == 201
    [entry] = (await client.get(f'{base(org, factory)}/entries')).json()['items']
    assert entry['body'] == body
    assert entry['content_type'] == 'json'
    assert entry['title'] is None


async def test_a_content_type_parameter_is_accepted(client, org, factory):
    response = await post_entry(
        client,
        f'{base(org, factory)}/entries',
        '# ok',
        'text/markdown; charset=utf-8',
    )

    assert response.status_code == 201


async def test_invalid_json_is_422_with_the_position(client, org, factory):
    response = await post_entry(
        client, f'{base(org, factory)}/entries', '{"a": }', 'application/json'
    )

    assert response.status_code == 422
    detail = response.json()['detail']
    assert detail['position'] == 6
    assert 'valid JSON' in detail['message']


@pytest.mark.parametrize('content_type', ['text/plain', 'application/xml', ''])
async def test_an_unsupported_content_type_is_415(client, org, factory, content_type):
    response = await post_entry(
        client, f'{base(org, factory)}/entries', 'x', content_type
    )

    assert response.status_code == 415


async def test_invalid_utf8_is_422(client, org, factory):
    response = await post_entry(client, f'{base(org, factory)}/entries', b'bad \xff')

    assert response.status_code == 422


async def test_a_body_over_the_limit_is_413(client, org, factory):
    with patch('server.services.worklog_service.WORKLOG_MAX_ENTRY_BYTES', 100):
        over = await post_entry(client, f'{base(org, factory)}/entries', 'x' * 101)
        exact = await post_entry(client, f'{base(org, factory)}/entries', 'x' * 100)

    assert over.status_code == 413
    assert exact.status_code == 201


async def test_a_streamed_body_over_the_limit_is_413_without_a_length_header(
    client, org, factory
):
    async def chunks():
        for _ in range(10):
            yield b'x' * 20

    with patch('server.services.worklog_service.WORKLOG_MAX_ENTRY_BYTES', 100):
        response = await client.post(
            f'{base(org, factory)}/entries',
            content=chunks(),
            headers={'Content-Type': 'text/markdown'},
        )

    assert response.status_code == 413


async def test_the_default_limit_is_64_kib(client, org, factory):
    over = await post_entry(
        client, f'{base(org, factory)}/entries', 'x' * (64 * 1024 + 1)
    )
    exact = await post_entry(client, f'{base(org, factory)}/entries', 'x' * (64 * 1024))

    assert (over.status_code, exact.status_code) == (413, 201)


@pytest.mark.parametrize(
    ('body', 'title'),
    [
        (
            '## INSTRUCTION: pause the workers\n\ndetails',
            'INSTRUCTION: pause the workers',
        ),
        ('Launched worker 3\nmore', 'Launched worker 3'),
        ('\n\n  first non-empty line', 'first non-empty line'),
        ('y' * 300, 'y' * 120),
    ],
)
async def test_markdown_titles_are_derived(client, org, factory, body, title):
    await post_entry(client, f'{base(org, factory)}/entries', body)

    [entry] = (await client.get(f'{base(org, factory)}/entries')).json()['items']
    assert entry['title'] == title


async def test_a_title_header_overrides_the_title_for_both_types(client, org, factory):
    url = f'{base(org, factory)}/entries'
    await post_entry(client, url, '## Derived', **{'X-Worklog-Title': 'Mine'})
    await post_entry(
        client, url, '{}', 'application/json', **{'X-Worklog-Title': 'Json title'}
    )

    titles = [e['title'] for e in (await client.get(url)).json()['items']]
    assert titles == ['Mine', 'Json title']


async def test_a_request_without_a_worklog_uses_the_default(client, org, factory):
    await client.post(f'{base(org, factory)}/worklogs', json={'name': 'Notes'})
    await post_entry(client, f'{base(org, factory)}/entries', 'to default')
    await post_entry(client, f'{base(org, factory)}/worklogs/notes/entries', 'to notes')

    default = await client.get(f'{base(org, factory)}/worklogs/default/entries')
    implicit = await client.get(f'{base(org, factory)}/entries')
    notes = await client.get(f'{base(org, factory)}/worklogs/notes/entries')

    assert [e['body'] for e in default.json()['items']] == ['to default']
    assert implicit.json() == default.json()
    assert [e['body'] for e in notes.json()['items']] == ['to notes']


async def test_an_unknown_worklog_name_is_404_and_never_falls_back(
    client, org, factory
):
    await client.get(f'{base(org, factory)}/worklogs')

    write = await post_entry(client, f'{base(org, factory)}/worklogs/typo/entries', 'x')
    read = await client.get(f'{base(org, factory)}/worklogs/typo/entries')

    assert (write.status_code, read.status_code) == (404, 404)
    default = await client.get(f'{base(org, factory)}/entries')
    assert default.json()['items'] == []


async def test_entries_page_in_order_without_skips_or_repeats(client, org, factory):
    url = f'{base(org, factory)}/entries'
    for number in range(5):
        await post_entry(client, url, f'entry {number}')

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {'limit': 2, **({'since': cursor} if cursor else {})}
        page = (await client.get(url, params=params)).json()
        pages += 1
        seen += [e['body'] for e in page['items']]
        if not page['has_more']:
            break
        cursor = page['next_cursor']

    assert seen == [f'entry {n}' for n in range(5)]
    assert pages == 3


async def test_a_cursor_after_the_last_entry_returns_the_new_ones(client, org, factory):
    url = f'{base(org, factory)}/entries'
    await post_entry(client, url, 'one')
    first = (await client.get(url)).json()
    await post_entry(client, url, 'two')

    page = (await client.get(url, params={'since': first['next_cursor']})).json()

    assert [e['body'] for e in page['items']] == ['two']


async def test_since_accepts_a_timestamp(client, org, factory):
    url = f'{base(org, factory)}/entries'
    old = await post_entry(client, url, 'old')
    new = await post_entry(client, url, 'new')

    page = (await client.get(url, params={'since': old.json()['created_at']})).json()

    assert [e['body'] for e in page['items']] == ['new']
    assert new.status_code == 201


async def test_paging_crosses_entries_with_identical_timestamps(
    client, org, factory, async_session_maker
):
    await client.get(f'{base(org, factory)}/worklogs')
    async with async_session_maker() as session:
        worklog_id = await session.scalar(select(Worklog.id))
        session.add_all(
            WorklogEntry(
                worklog_id=worklog_id,
                content_type='markdown',
                title=None,
                author_kind='user',
                author_id=USER_ID,
                body=f'same time {n}',
            )
            for n in range(5)
        )
        await session.commit()

    seen: list[str] = []
    cursor = None
    for _ in range(5):
        params = {'limit': 2, **({'since': cursor} if cursor else {})}
        page = (await client.get(f'{base(org, factory)}/entries', params=params)).json()
        seen += [e['body'] for e in page['items']]
        cursor = page['next_cursor']

    assert seen == [f'same time {n}' for n in range(5)]
    stamps = {
        e['created_at']
        for e in (await client.get(f'{base(org, factory)}/entries')).json()['items']
    }
    assert len(stamps) == 1


async def test_content_type_filters_the_list(client, org, factory):
    url = f'{base(org, factory)}/entries'
    await post_entry(client, url, 'md')
    await post_entry(client, url, '{"a": 1}', 'application/json')

    markdown = (await client.get(url, params={'content_type': 'markdown'})).json()
    json_only = (await client.get(url, params={'content_type': 'json'})).json()

    assert [e['body'] for e in markdown['items']] == ['md']
    assert [e['body'] for e in json_only['items']] == ['{"a": 1}']
    bad = await client.get(url, params={'content_type': 'xml'})
    assert bad.status_code == 422


@pytest.mark.parametrize('params', [{'limit': 0}, {'limit': 201}, {'since': 'bad!'}])
async def test_bad_listing_parameters_are_422(client, org, factory, params):
    response = await client.get(f'{base(org, factory)}/entries', params=params)

    assert response.status_code == 422


async def test_there_is_no_route_to_change_or_remove_an_entry(client, org, factory):
    created = await post_entry(client, f'{base(org, factory)}/entries', 'keep')
    entry_url = f'{base(org, factory)}/entries/{created.json()["id"]}'

    results = [
        (await client.request(method, entry_url)).status_code
        for method in ('PUT', 'PATCH', 'DELETE', 'GET')
    ]

    assert results == [404, 404, 404, 404]


# --- Who wrote it ---


async def test_a_user_entry_records_the_user_and_ignores_a_supplied_author(
    client, org, factory
):
    await post_entry(
        client,
        f'{base(org, factory)}/entries?author_kind=automation&author_id=evil&run_id={RUN_ID}',
        'x',
        **{
            'X-Worklog-Author': 'evil',
            'X-Worklog-Author-Kind': 'automation',
            'X-Worklog-Author-Id': 'evil',
        },
    )

    [entry] = (await client.get(f'{base(org, factory)}/entries')).json()['items']
    assert entry['author'] == {'kind': 'user', 'id': USER_ID, 'name': None}
    assert entry['run_id'] is None


async def test_a_run_entry_records_the_automation_and_the_run(client, org, factory):
    created = await post_entry(
        client,
        '/api/service/worklog/entries?author_kind=user',
        '## from a run',
        **run_headers(org, factory),
    )

    assert created.status_code == 201, created.text
    [entry] = (
        await client.get(
            '/api/service/worklog/entries', headers=run_headers(org, factory)
        )
    ).json()['items']
    assert entry['author'] == {
        'kind': 'automation',
        'id': str(AUTOMATION_ID),
        'name': 'Orchestrator',
    }
    assert entry['run_id'] == str(RUN_ID)


# --- Summaries ---


async def test_summaries_report_zeros_for_a_never_used_factory_and_create_nothing(
    client, org, factory, async_session_maker
):
    response = await client.get(
        f'/api/organizations/{org.id}/worklog-summaries',
        params={'factory_id': str(factory)},
    )

    assert response.status_code == 200, response.text
    assert response.json()['items'] == [
        {
            'factory_id': str(factory),
            'worklog_count': 0,
            'entry_count': 0,
            'last_entry_at': None,
        }
    ]
    assert await count(async_session_maker, Worklog) == 0


async def test_summaries_total_worklogs_and_entries_per_factory(
    client, org, factory, directory
):
    other = directory.add(org.id)
    await client.post(f'{base(org, factory)}/worklogs', json={'name': 'Notes'})
    await post_entry(client, f'{base(org, factory)}/entries', 'a')
    last = await post_entry(client, f'{base(org, factory)}/worklogs/notes/entries', 'b')

    response = await client.get(
        f'/api/organizations/{org.id}/worklog-summaries',
        params=[('factory_id', str(factory)), ('factory_id', str(other))],
    )

    first, second = response.json()['items']
    assert first == {
        'factory_id': str(factory),
        'worklog_count': 2,
        'entry_count': 2,
        'last_entry_at': last.json()['created_at'],
    }
    assert second['worklog_count'] == 0


async def test_summaries_do_not_cross_orgs(client, org, create_org, directory):
    other_org = create_org()
    foreign = directory.add(other_org.id)

    response = await client.get(
        f'/api/organizations/{org.id}/worklog-summaries',
        params={'factory_id': str(foreign)},
    )

    assert response.json()['items'][0]['worklog_count'] == 0


@pytest.mark.parametrize('count_', [0, 101])
async def test_summaries_need_between_1_and_100_factories(client, org, count_):
    params = [('factory_id', str(uuid.uuid4())) for _ in range(count_)]

    response = await client.get(
        f'/api/organizations/{org.id}/worklog-summaries', params=params
    )

    assert response.status_code == 422


async def test_summaries_accept_100_factories(client, org):
    params = [('factory_id', str(uuid.uuid4())) for _ in range(100)]

    response = await client.get(
        f'/api/organizations/{org.id}/worklog-summaries', params=params
    )

    assert response.status_code == 200
    assert len(response.json()['items']) == 100


# --- Authorization ---


def _member_write_routes(org, factory):
    b = base(org, factory)
    return [
        ('POST', f'{b}/worklogs', {'json': {'name': 'x'}}),
        ('PATCH', f'{b}/worklogs/default', {'json': {'name': 'x'}}),
        ('DELETE', f'{b}/worklogs/default', {}),
        (
            'POST',
            f'{b}/entries',
            {'content': b'x', 'headers': {'Content-Type': 'text/markdown'}},
        ),
        (
            'POST',
            f'{b}/worklogs/default/entries',
            {'content': b'x', 'headers': {'Content-Type': 'text/markdown'}},
        ),
    ]


async def test_a_member_can_read_but_not_write(client, org, factory, role):
    role.name = 'member'
    b = base(org, factory)

    reads = [
        (await client.get(url)).status_code
        for url in (
            f'{b}/worklogs',
            f'{b}/entries',
            f'{b}/worklogs/default/entries',
            f'/api/organizations/{org.id}/worklog-summaries?factory_id={factory}',
        )
    ]
    writes = [
        (await client.request(m, url, **kwargs)).status_code
        for m, url, kwargs in _member_write_routes(org, factory)
    ]

    assert reads == [200, 200, 200, 200]
    assert writes == [403] * 5


@pytest.mark.parametrize('role_name', ['admin', 'owner'])
async def test_admins_and_owners_can_write(client, org, factory, role, role_name):
    role.name = role_name

    response = await post_entry(client, f'{base(org, factory)}/entries', 'x')

    assert response.status_code == 201


async def test_a_non_member_is_403_everywhere(client, org, factory, role):
    role.name = None

    responses = [
        await client.get(f'{base(org, factory)}/worklogs'),
        await client.get(
            f'/api/organizations/{org.id}/worklog-summaries?factory_id={factory}'
        ),
        await post_entry(client, f'{base(org, factory)}/entries', 'x'),
    ]

    assert [r.status_code for r in responses] == [403, 403, 403]


async def test_an_unauthenticated_caller_is_401(client, app, org, factory):
    app.dependency_overrides[get_user_id] = lambda: None

    response = await client.get(f'{base(org, factory)}/worklogs')

    assert response.status_code == 401


# --- Run token routes ---


async def test_a_run_lists_its_factorys_worklogs_without_a_lookup(
    client, org, factory, directory
):
    response = await client.get(
        '/api/service/worklog', headers=run_headers(org, factory)
    )

    assert response.status_code == 200, response.text
    assert [i['slug'] for i in response.json()['items']] == ['default']
    assert directory.requests == []


async def test_a_run_writes_and_reads_default_and_named_worklogs(client, org, factory):
    await client.post(f'{base(org, factory)}/worklogs', json={'name': 'Notes'})
    headers = run_headers(org, factory)

    default = await post_entry(client, '/api/service/worklog/entries', 'd', **headers)
    named = await post_entry(
        client, '/api/service/worklog/notes/entries', 'n', **headers
    )
    json_entry = await post_entry(
        client,
        '/api/service/worklog/notes/entries',
        '{"ok": true}',
        'application/json',
        **headers,
    )

    assert [r.status_code for r in (default, named, json_entry)] == [201, 201, 201]
    read_default = await client.get('/api/service/worklog/entries', headers=headers)
    read_named = await client.get(
        '/api/service/worklog/notes/entries',
        params={'content_type': 'json'},
        headers=headers,
    )
    assert [e['body'] for e in read_default.json()['items']] == ['d']
    assert [e['body'] for e in read_named.json()['items']] == ['{"ok": true}']
    missing = await client.get('/api/service/worklog/typo/entries', headers=headers)
    assert missing.status_code == 404


async def test_a_run_reaches_only_its_own_factory(client, org, factory, directory):
    other = directory.add(org.id)
    await post_entry(client, f'{base(org, other)}/entries', 'other factory')

    mine = await client.get(
        '/api/service/worklog/entries', headers=run_headers(org, factory)
    )
    theirs = await client.get(
        '/api/service/worklog/entries', headers=run_headers(org, other)
    )

    assert mine.json()['items'] == []
    assert [e['body'] for e in theirs.json()['items']] == ['other factory']


@pytest.mark.parametrize(
    'headers',
    [
        pytest.param(lambda org, f: {}, id='missing'),
        pytest.param(lambda org, f: {'Authorization': 'Bearer nope'}, id='garbage'),
        pytest.param(
            lambda org, f: run_headers(
                org, f, exp=int(datetime.now(timezone.utc).timestamp()) - 5
            ),
            id='expired',
        ),
        pytest.param(lambda org, f: run_headers(org, f, aud='kv'), id='wrong-audience'),
        pytest.param(
            lambda org, f: {'Authorization': f'Bearer {mint(org, f, key="other-key")}'},
            id='wrong-signature',
        ),
        pytest.param(lambda org, f: {'Authorization': 'Basic abc'}, id='not-bearer'),
    ],
)
async def test_a_bad_run_token_is_401(client, org, factory, headers):
    response = await client.get('/api/service/worklog', headers=headers(org, factory))

    assert response.status_code == 401


async def test_a_user_session_or_api_key_is_not_a_run_token(client, org, factory):
    response = await client.get(
        '/api/service/worklog',
        headers={'Authorization': 'Bearer sk-oh-looks-like-an-api-key'},
        cookies={'keycloak_auth': 'a-session'},
    )

    assert response.status_code == 401


async def test_run_routes_are_503_without_the_service_key(
    client, org, factory, monkeypatch
):
    headers = run_headers(org, factory)
    monkeypatch.delenv('AUTOMATIONS_SERVICE_KEY')

    response = await client.get('/api/service/worklog', headers=headers)

    assert response.status_code == 503


async def test_user_routes_work_without_the_service_key_for_known_worklogs(
    client, org, factory, monkeypatch
):
    await client.get(f'{base(org, factory)}/worklogs')
    monkeypatch.delenv('AUTOMATIONS_SERVICE_KEY')

    response = await client.get(f'{base(org, factory)}/entries')

    assert response.status_code == 200


async def test_the_run_token_routes_apply_the_write_limits(client, org, factory):
    headers = run_headers(org, factory)
    with patch('server.services.worklog_service.WORKLOG_MAX_ENTRY_BYTES', 10):
        big = await post_entry(
            client, '/api/service/worklog/entries', 'x' * 11, **headers
        )
        wrong_type = await post_entry(
            client, '/api/service/worklog/entries', 'x', 'text/plain', **headers
        )
        bad_json = await post_entry(
            client, '/api/service/worklog/entries', '{', 'application/json', **headers
        )

    assert [big.status_code, wrong_type.status_code, bad_json.status_code] == [
        413,
        415,
        422,
    ]


# --- License gating ---


def _every_route(org, factory) -> list[tuple[str, str, dict]]:
    b = base(org, factory)
    markdown = {'content': b'x', 'headers': {'Content-Type': 'text/markdown'}}
    run = {'headers': run_headers(org, factory)}
    run_write = {
        'content': b'x',
        'headers': {**run['headers'], 'Content-Type': 'text/markdown'},
    }
    return [
        ('GET', f'{b}/worklogs', {}),
        ('POST', f'{b}/worklogs', {'json': {'name': 'x'}}),
        ('PATCH', f'{b}/worklogs/default', {'json': {'name': 'x'}}),
        ('DELETE', f'{b}/worklogs/default', {}),
        ('POST', f'{b}/worklogs/default/entries', markdown),
        ('GET', f'{b}/worklogs/default/entries', {}),
        ('POST', f'{b}/entries', markdown),
        ('GET', f'{b}/entries', {}),
        (
            'GET',
            f'/api/organizations/{org.id}/worklog-summaries?factory_id={factory}',
            {},
        ),
        ('GET', '/api/service/worklog', run),
        ('GET', '/api/service/worklog/entries', run),
        ('POST', '/api/service/worklog/entries', run_write),
        ('GET', '/api/service/worklog/default/entries', run),
        ('POST', '/api/service/worklog/default/entries', run_write),
    ]


async def test_an_unlicensed_install_gets_403_on_every_worklog_route(
    client, org, factory, monkeypatch
):
    service = install_license(monkeypatch)
    await service.check_now()

    statuses = [
        (await client.request(method, url, **kwargs)).status_code
        for method, url, kwargs in _every_route(org, factory)
    ]

    assert statuses == [403] * 14


async def test_a_license_with_the_worklogs_feature_allows_the_routes(
    client, org, factory, monkeypatch
):
    install_license(monkeypatch)
    with patch.object(
        LicenseService,
        'has_feature',
        lambda self, feature: feature == 'worklogs',
    ):
        responses = [
            await client.get(f'{base(org, factory)}/worklogs'),
            await client.get('/api/service/worklog', headers=run_headers(org, factory)),
        ]

    assert [r.status_code for r in responses] == [200, 200]


async def test_a_license_without_the_worklogs_feature_is_403(
    client, org, factory, monkeypatch
):
    install_license(monkeypatch)
    with patch.object(
        LicenseService, 'has_feature', lambda self, feature: feature == 'sso'
    ):
        response = await client.get(f'{base(org, factory)}/worklogs')

    assert response.status_code == 403
    assert 'worklogs' in response.json()['detail']


async def test_with_enforcement_off_the_routes_work(client, org, factory):
    response = await client.get(f'{base(org, factory)}/worklogs')

    assert response.status_code == 200


async def test_authentication_is_checked_before_the_license(
    client, app, org, factory, monkeypatch
):
    service = install_license(monkeypatch)
    await service.check_now()
    app.dependency_overrides[get_user_id] = lambda: None

    user = await client.get(f'{base(org, factory)}/worklogs')
    run = await client.get(
        '/api/service/worklog', headers={'Authorization': 'Bearer nope'}
    )

    assert (user.status_code, run.status_code) == (401, 401)


# --- Write rate limit ---


def _memory_limiter(windows: str) -> RateLimiter:
    storage = limits.aio.storage.MemoryStorage()
    return RateLimiter(limits.aio.strategies.FixedWindowRateLimiter(storage), windows)


async def test_writes_beyond_the_limit_are_429_with_retry_after(client, org, factory):
    url = f'{base(org, factory)}/entries'
    with patch(
        'server.routes.worklogs.write_rate_limiter', _memory_limiter('2/minute')
    ):
        statuses = [(await post_entry(client, url, 'x')).status_code for _ in range(3)]
        limited = await post_entry(client, url, 'x')
        read = await client.get(url)

    assert statuses == [201, 201, 429]
    assert limited.headers['Retry-After'].isdigit()
    assert 'X-RateLimit-Limit' in limited.headers
    assert read.status_code == 200


async def test_the_limit_is_per_factory_and_shared_by_both_doors(
    client, org, factory, directory
):
    other = directory.add(org.id)
    with patch(
        'server.routes.worklogs.write_rate_limiter', _memory_limiter('2/minute')
    ):
        user = await post_entry(client, f'{base(org, factory)}/entries', 'x')
        run = await post_entry(
            client, '/api/service/worklog/entries', 'x', **run_headers(org, factory)
        )
        third = await post_entry(client, f'{base(org, factory)}/entries', 'x')
        elsewhere = await post_entry(client, f'{base(org, other)}/entries', 'x')

    assert [r.status_code for r in (user, run, third, elsewhere)] == [
        201,
        201,
        429,
        201,
    ]


async def test_a_failing_limiter_lets_writes_through(client, org, factory):
    broken = _memory_limiter('1/minute')
    broken.strategy.hit = AsyncMock(side_effect=ConnectionError('redis is down'))
    url = f'{base(org, factory)}/entries'

    with patch('server.routes.worklogs.write_rate_limiter', broken):
        statuses = [(await post_entry(client, url, 'x')).status_code for _ in range(3)]

    assert statuses == [201, 201, 201]


def test_an_empty_write_limit_setting_disables_the_limiter():
    from server.rate_limit import create_redis_rate_limiter

    assert create_redis_rate_limiter('') is None


def test_the_default_limits():
    from server.services import worklog_service

    assert worklog_service.WORKLOG_WRITE_RATE_LIMIT == '10/second; 300/minute'
    assert worklog_service.WORKLOG_MAX_ENTRY_BYTES == 64 * 1024
