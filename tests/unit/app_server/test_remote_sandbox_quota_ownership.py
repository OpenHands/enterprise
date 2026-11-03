"""Quota cleanup must never use ADMIN's visibility as an ownership boundary."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from openhands.app_server.errors import SandboxError
from openhands.app_server.sandbox.preset_sandbox_spec_service import (
    PresetSandboxSpecService,
)
from openhands.app_server.sandbox.remote_sandbox_service import RemoteSandboxService
from openhands.app_server.sandbox.sandbox_spec_models import SandboxSpecInfo
from openhands.app_server.sandbox.sandbox_store import (
    DOCKER_BACKEND,
    REMOTE_BACKEND,
    StoredSandbox,
    hash_session_api_key,
)
from openhands.app_server.user.specifiy_user_context import ADMIN, SpecifyUserContext


@pytest.fixture
async def quota_service(async_session_maker):
    runtimes = {}
    requests = []

    def respond(request):
        requests.append(request)
        path = request.url.path
        if path == '/list':
            if runtimes.get('target', {}).get('concurrent_resume'):
                runtimes['target']['status'] = 'running'
            return httpx.Response(
                200,
                json={
                    'runtimes': [
                        r for r in runtimes.values() if r['status'] == 'running'
                    ]
                },
            )
        if path.startswith('/sessions/'):
            runtime = runtimes.get(path.rsplit('/', 1)[1])
            return httpx.Response(200, json=runtime) if runtime else httpx.Response(404)
        if path in ('/pause', '/resume'):
            runtime_id = json.loads(request.content)['runtime_id']
            runtime = next(
                r for r in runtimes.values() if r['runtime_id'] == runtime_id
            )
            runtime['status'] = 'paused' if path == '/pause' else 'running'
            return httpx.Response(200, json={'session_api_key': 'rotated-key'})
        if path == '/start':
            sandbox_id = json.loads(request.content)['session_id']
            return httpx.Response(
                200,
                json={
                    'session_id': sandbox_id,
                    'runtime_id': f'rt-{sandbox_id}',
                    'status': 'running',
                    'session_api_key': 'new-key',
                },
            )
        raise AssertionError(f'Unexpected request: {request.method} {path}')

    async with (
        async_session_maker() as session,
        httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client,
    ):
        service = RemoteSandboxService(
            sandbox_spec_service=PresetSandboxSpecService(
                [SandboxSpecInfo(id='test-image', command=['agent-server'])]
            ),
            api_url='https://runtime.test',
            api_key='test-key',
            web_url='https://app.test',
            resource_factor=1,
            runtime_class='gvisor',
            start_sandbox_timeout=120,
            max_num_sandboxes=2,
            user_context=ADMIN,
            httpx_client=client,
            db_session=session,
        )
        yield service, runtimes, requests


async def seed(service, runtimes, target_owner='owner', target_status='paused'):
    rows = [
        ('target', target_owner, REMOTE_BACKEND),
        ('other-user-oldest', 'other', REMOTE_BACKEND),
        ('owner-old', 'owner', REMOTE_BACKEND),
        ('owner-new', 'owner', REMOTE_BACKEND),
        ('other-user-new', 'other', REMOTE_BACKEND),
        ('owner-docker', 'owner', DOCKER_BACKEND),
        ('owner-stale', 'owner', REMOTE_BACKEND),
        ('ownerless', None, REMOTE_BACKEND),
    ]
    for index, (sandbox_id, owner, backend) in enumerate(rows):
        service.db_session.add(
            StoredSandbox(
                id=sandbox_id,
                backend=backend,
                created_by_user_id=owner,
                sandbox_spec_id='test-image',
                session_api_key_hash=hash_session_api_key('original-key'),
                created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
            )
        )
        if sandbox_id != 'owner-stale':
            runtimes[sandbox_id] = {
                'session_id': sandbox_id,
                'runtime_id': f'rt-{sandbox_id}',
                'status': target_status if sandbox_id == 'target' else 'running',
                'session_api_key': 'original-key',
            }
    await service.db_session.flush()


def paused_runtime_ids(requests):
    return [
        json.loads(r.content)['runtime_id'] for r in requests if r.url.path == '/pause'
    ]


@pytest.mark.parametrize('caller', [ADMIN, SpecifyUserContext('owner')])
@pytest.mark.parametrize('concurrent_resume', [False, True])
async def test_resume_only_pauses_the_owners_oldest_sandbox(
    quota_service, caller, concurrent_resume
):
    service, runtimes, requests = quota_service
    await seed(service, runtimes)
    service.user_context = caller
    runtimes['target']['concurrent_resume'] = concurrent_resume

    assert await service.resume_sandbox('target')

    assert paused_runtime_ids(requests) == ['rt-owner-old']
    for sandbox_id in (
        'other-user-oldest',
        'other-user-new',
        'owner-docker',
        'ownerless',
    ):
        assert runtimes[sandbox_id]['status'] == 'running'
        row = await service.db_session.get(StoredSandbox, sandbox_id)
        assert row.session_api_key_hash == hash_session_api_key('original-key')
    target = await service.db_session.get(StoredSandbox, 'target')
    assert target.session_api_key_hash == hash_session_api_key('rotated-key')


async def test_resume_under_owner_limit_does_not_pause_other_users(quota_service):
    service, runtimes, requests = quota_service
    await seed(service, runtimes)
    service.max_num_sandboxes = 3

    assert await service.resume_sandbox('target')

    assert paused_runtime_ids(requests) == []


@pytest.mark.parametrize('owner', [None, ''])
async def test_ownerless_resume_fails_before_cleanup(quota_service, owner):
    service, runtimes, requests = quota_service
    await seed(service, runtimes, target_owner=owner)

    with pytest.raises(SandboxError, match='owner'):
        await service.resume_sandbox('target')

    assert all(r.method == 'GET' and r.url.path != '/list' for r in requests)


async def test_admin_cannot_start_without_a_quota_owner(quota_service):
    service, runtimes, requests = quota_service
    await seed(service, runtimes)

    with pytest.raises(SandboxError, match='owner'):
        await service.start_sandbox(sandbox_id='new-sandbox')

    assert requests == []


async def test_user_start_preserves_other_users_sandboxes(quota_service):
    service, runtimes, requests = quota_service
    await seed(service, runtimes)
    del runtimes['target']
    service.user_context = SpecifyUserContext('owner')

    info = await service.start_sandbox(sandbox_id='new-sandbox')

    assert info.id == 'new-sandbox'
    assert paused_runtime_ids(requests) == ['rt-owner-old']


@pytest.mark.parametrize('runtime_status', ['starting', 'running'])
async def test_admin_resume_of_active_target_has_no_cleanup(
    quota_service, runtime_status
):
    service, runtimes, requests = quota_service
    await seed(service, runtimes, target_status=runtime_status)

    assert await service.resume_sandbox('target')

    assert [r.url.path for r in requests] == ['/sessions/target']


async def test_unauthorized_resume_cannot_trigger_cleanup(quota_service):
    service, runtimes, requests = quota_service
    await seed(service, runtimes)
    service.user_context = SpecifyUserContext('other')

    assert not await service.resume_sandbox('target')

    assert requests == []


async def test_explicit_quota_owner_does_not_bypass_caller_access(quota_service):
    service, runtimes, requests = quota_service
    await seed(service, runtimes)
    service.user_context = SpecifyUserContext('other')

    assert await service.pause_old_sandboxes(0, owner_user_id='owner') == []

    assert paused_runtime_ids(requests) == []


async def test_admin_cleanup_requires_an_explicit_owner(quota_service):
    service, runtimes, requests = quota_service
    await seed(service, runtimes)

    with pytest.raises(SandboxError, match='owner'):
        await service.pause_old_sandboxes(0)

    assert requests == []
