"""The purge the automation service calls after it deletes a factory, and the
maintenance task that repairs a missed one."""

import uuid
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from server.licensing import service as license_module
from server.licensing.config import LicenseConfig
from server.licensing.service import LicenseService
from server.maintenance_task_processor import worklog_orphan_purge_processor as purge
from server.maintenance_task_processor.worklog_orphan_purge_processor import (
    WorklogOrphanPurgeProcessor,
    enqueue_worklog_orphan_purge_task,
)
from server.routes.service import service_router
from storage.maintenance_task import MaintenanceTask, MaintenanceTaskStatus
from storage.worklog import Worklog, WorklogEntry
from storage.worklog_store import EntryAuthor, WorklogStore

SERVICE_KEY = 'purge-test-service-key'
AUTOMATION_URL = 'https://automation.test'


@pytest.fixture(autouse=True)
def database(async_session_maker, monkeypatch):
    monkeypatch.setattr('storage.worklog_store.a_session_maker', async_session_maker)


@pytest.fixture
async def client():
    app = FastAPI()
    app.include_router(service_router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as client:
        yield client


@pytest.fixture
def org(create_org):
    return create_org()


async def _seed(org, factory) -> None:
    store = WorklogStore()
    await store.ensure_default(org.id, factory)
    await store.add_entry(
        org.id,
        factory,
        None,
        content_type='markdown',
        title=None,
        body='x',
        author=EntryAuthor('user', 'u', None),
        run_id=None,
    )


async def _count(async_session_maker, model) -> int:
    async with async_session_maker() as session:
        return await session.scalar(select(func.count()).select_from(model))


def _url(org_id, factory_id) -> str:
    return f'/api/service/orgs/{org_id}/factories/{factory_id}/worklogs'


# --- The purge route ---


async def test_purge_removes_the_factorys_worklogs_and_entries(
    client, org, async_session_maker
):
    gone, kept = uuid.uuid4(), uuid.uuid4()
    await _seed(org, gone)
    await _seed(org, kept)

    with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', SERVICE_KEY):
        response = await client.delete(
            _url(org.id, gone), headers={'X-Service-API-Key': SERVICE_KEY}
        )

    assert response.status_code == 204
    assert response.content == b''
    assert await _count(async_session_maker, Worklog) == 1
    assert await _count(async_session_maker, WorklogEntry) == 1


async def test_purge_is_idempotent(client, org):
    factory = uuid.uuid4()
    await _seed(org, factory)

    with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', SERVICE_KEY):
        statuses = [
            (
                await client.delete(
                    _url(org.id, factory), headers={'X-Service-API-Key': SERVICE_KEY}
                )
            ).status_code
            for _ in range(2)
        ]
        unknown = await client.delete(
            _url(org.id, uuid.uuid4()), headers={'X-Service-API-Key': SERVICE_KEY}
        )

    assert statuses == [204, 204]
    assert unknown.status_code == 204


async def test_purge_leaves_another_orgs_worklogs_alone(
    client, org, create_org, async_session_maker
):
    other_org = create_org()
    factory = uuid.uuid4()
    await _seed(other_org, factory)

    with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', SERVICE_KEY):
        response = await client.delete(
            _url(org.id, factory), headers={'X-Service-API-Key': SERVICE_KEY}
        )

    assert response.status_code == 204
    assert await _count(async_session_maker, Worklog) == 1


@pytest.mark.parametrize(
    'headers',
    [{}, {'X-Service-API-Key': 'wrong'}, {'Authorization': f'Bearer {SERVICE_KEY}'}],
)
async def test_purge_needs_the_service_key(client, org, async_session_maker, headers):
    factory = uuid.uuid4()
    await _seed(org, factory)

    with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', SERVICE_KEY):
        response = await client.delete(_url(org.id, factory), headers=headers)

    assert response.status_code == 401
    assert await _count(async_session_maker, Worklog) == 1


async def test_purge_is_503_when_service_auth_is_not_configured(client, org):
    with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', ''):
        response = await client.delete(
            _url(org.id, uuid.uuid4()), headers={'X-Service-API-Key': SERVICE_KEY}
        )

    assert response.status_code == 503


async def test_purge_is_not_license_gated(
    client, org, monkeypatch, async_session_maker
):
    service = LicenseService(
        LicenseConfig(
            enforcement_enabled=True,
            license_key=None,
            license_server_url='https://cc.example',
            check_interval_seconds=1800,
        )
    )
    monkeypatch.setattr(license_module, '_license_service', service)
    await service.check_now()
    factory = uuid.uuid4()
    await _seed(org, factory)

    with patch('server.routes.service.AUTOMATIONS_SERVICE_KEY', SERVICE_KEY):
        response = await client.delete(
            _url(org.id, factory), headers={'X-Service-API-Key': SERVICE_KEY}
        )

    assert response.status_code == 204
    assert await _count(async_session_maker, Worklog) == 0


# --- The maintenance task ---


class FactoryDirectory:
    def __init__(self) -> None:
        self.known: set[uuid.UUID] = set()
        self.fail_for_orgs: set[uuid.UUID] = set()
        self.requests: list[httpx.Request] = []

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        org_id = uuid.UUID(request.url.params['org_id'])
        if org_id in self.fail_for_orgs:
            return httpx.Response(500)
        wanted = [uuid.UUID(v) for v in request.url.params.get_list('id')]
        return httpx.Response(
            200,
            json={
                'factories': [
                    {'id': str(f), 'org_id': str(org_id)}
                    for f in wanted
                    if f in self.known
                ]
            },
        )


@pytest.fixture
def directory(monkeypatch) -> FactoryDirectory:
    directory = FactoryDirectory()
    monkeypatch.setenv('AUTOMATIONS_SERVICE_KEY', SERVICE_KEY)
    with (
        patch(
            'server.maintenance_task_processor.worklog_orphan_purge_processor.AUTOMATION_SERVICE_URL',
            AUTOMATION_URL,
        ),
        patch('server.services.worklog_service.AUTOMATION_SERVICE_URL', AUTOMATION_URL),
        patch(
            'server.services.worklog_service._http_client',
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(directory.respond)),
        ),
    ):
        yield directory


async def _run(task: MaintenanceTask | None = None) -> dict:
    return await WorklogOrphanPurgeProcessor()(task or MaintenanceTask())


async def test_the_task_removes_worklogs_of_factories_that_are_gone_and_keeps_the_rest(
    org, directory, async_session_maker
):
    alive, dead = uuid.uuid4(), uuid.uuid4()
    await _seed(org, alive)
    await _seed(org, dead)
    directory.known.add(alive)

    info = await _run()

    assert info == {'checked': 2, 'purged': 1, 'error_count': 0}
    async with async_session_maker() as session:
        remaining = (await session.scalars(select(Worklog.factory_id))).all()
    assert remaining == [alive]


async def test_the_task_checks_each_org_separately(
    org, create_org, directory, async_session_maker
):
    other_org = create_org()
    one, two = uuid.uuid4(), uuid.uuid4()
    await _seed(org, one)
    await _seed(other_org, two)
    directory.known.update({one, two})

    info = await _run()

    assert info['purged'] == 0
    assert {r.url.params['org_id'] for r in directory.requests} == {
        str(org.id),
        str(other_org.id),
    }


async def test_the_task_never_purges_when_a_lookup_fails(
    org, create_org, directory, async_session_maker
):
    other_org = create_org()
    failing, working = uuid.uuid4(), uuid.uuid4()
    await _seed(org, failing)
    await _seed(other_org, working)
    directory.fail_for_orgs.add(org.id)

    info = await _run()

    assert info['error_count'] == 1
    assert info['purged'] == 1
    async with async_session_maker() as session:
        remaining = (await session.scalars(select(Worklog.factory_id))).all()
    assert remaining == [failing]


async def test_the_task_reports_an_error_count_that_fails_the_task_status(
    org, directory
):
    from run_maintenance_tasks import maintenance_task_status

    await _seed(org, uuid.uuid4())
    directory.fail_for_orgs.add(org.id)

    info = await _run()

    assert maintenance_task_status(info) == MaintenanceTaskStatus.ERROR


async def test_the_task_walks_every_batch(org, directory, async_session_maker):
    factories = [uuid.uuid4() for _ in range(5)]
    for factory in factories:
        await _seed(org, factory)
    directory.known.add(factories[0])

    with patch.object(purge, 'FACTORY_BATCH_SIZE', 2):
        info = await _run()

    assert info == {'checked': 5, 'purged': 4, 'error_count': 0}
    assert await _count(async_session_maker, Worklog) == 1


async def test_the_task_does_nothing_without_the_automation_service(
    org, async_session_maker, monkeypatch
):
    await _seed(org, uuid.uuid4())
    monkeypatch.delenv('AUTOMATIONS_SERVICE_KEY', raising=False)

    info = await _run()

    assert info['error_count'] == 0
    assert 'skipped' in info
    assert await _count(async_session_maker, Worklog) == 1


async def test_the_task_needs_no_license(
    org, directory, monkeypatch, async_session_maker
):
    service = LicenseService(
        LicenseConfig(
            enforcement_enabled=True,
            license_key=None,
            license_server_url='https://cc.example',
            check_interval_seconds=1800,
        )
    )
    monkeypatch.setattr(license_module, '_license_service', service)
    await service.check_now()
    await _seed(org, uuid.uuid4())

    info = await _run()

    assert info['purged'] == 1
    assert await _count(async_session_maker, Worklog) == 0


def test_enqueue_adds_one_pending_task_only_when_worklogs_exist(
    session_maker, org, monkeypatch
):
    monkeypatch.setattr(purge, 'session_maker', session_maker)
    assert enqueue_worklog_orphan_purge_task() is False

    with session_maker() as session:
        session.add(
            Worklog(
                id=uuid.uuid4(),
                org_id=org.id,
                factory_id=uuid.uuid4(),
                slug='default',
                name='Default',
                is_default=True,
            )
        )
        session.commit()

    assert enqueue_worklog_orphan_purge_task() is True
    assert enqueue_worklog_orphan_purge_task() is False
    with session_maker() as session:
        tasks = session.query(MaintenanceTask).all()
        assert [t.status for t in tasks] == [MaintenanceTaskStatus.PENDING]
        assert isinstance(tasks[0].get_processor(), WorklogOrphanPurgeProcessor)
