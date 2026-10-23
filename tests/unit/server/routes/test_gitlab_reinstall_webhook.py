"""POST /integration/gitlab/reinstall-webhook, against a real database.

GitLab is the one fake (``tests.unit.gitlab_fakes``).
"""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from sqlalchemy import func, update

import server.routes.integration.gitlab as route_module
import storage.gitlab_webhook_store as store_module
import sync.install_gitlab_webhooks as job_module
from integrations.gitlab.webhook_installation import (
    CLAIM_LEASE,
    BreakLoopException,
    install_claimed_webhook,
)
from integrations.types import GitLabResourceType
from server.routes.integration.gitlab import (
    ReinstallWebhookRequest,
    ResourceIdentifier,
    reinstall_gitlab_webhook,
)
from storage.gitlab_webhook import GitlabWebhook, WebhookStatus
from storage.gitlab_webhook_store import GitlabWebhookStore
from sync.install_gitlab_webhooks import VerifyWebhookStatus
from tests.unit.gitlab_fakes import USER, FakeGitLab, assert_delivery_authenticates

ADMIN = 'admin-2'
PROJECT = 'project-1'


@pytest.fixture
def gitlab():
    return FakeGitLab()


@pytest.fixture
def store(async_session_maker, gitlab, monkeypatch):
    monkeypatch.setattr(store_module, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(job_module, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(job_module, 'GitLabServiceImpl', gitlab.for_user)
    monkeypatch.setattr(route_module, 'GitLabServiceImpl', gitlab.for_user)
    monkeypatch.setattr(route_module, 'SaaSGitLabService', FakeGitLab)
    return GitlabWebhookStore()


@pytest.fixture
def add_row(async_session_maker):
    async def add(**fields):
        fields.setdefault('webhook_exists', False)
        row = GitlabWebhook(user_id=USER, project_id=PROJECT, **fields)
        async with async_session_maker() as session, session.begin():
            session.add(row)
        return row.id

    return add


@pytest.fixture
def get_row(async_session_maker):
    async def get(row_id):
        async with async_session_maker() as session:
            return await session.get(GitlabWebhook, row_id)

    return get


def reinstall(user_id=ADMIN):
    body = ReinstallWebhookRequest(
        resource=ResourceIdentifier(type=GitLabResourceType.PROJECT, id=PROJECT)
    )
    return reinstall_gitlab_webhook(body=body, user_id=user_id)


async def run_job():
    await VerifyWebhookStatus().install_webhooks()


async def test_reinstall_over_an_existing_hook_leaves_one_that_authenticates(
    store, gitlab, add_row, get_row
):
    row_id = await add_row(
        webhook_exists=True, webhook_uuid='uuid-1', webhook_secret='secret-1'
    )
    gitlab.add_hook(PROJECT, secret='secret-1', uuid='uuid-1', user=USER)

    result = await reinstall(ADMIN)

    assert result.success
    [hook] = gitlab.hooks[PROJECT]
    assert hook['user'] == ADMIN
    await assert_delivery_authenticates(store, hook)
    row = await get_row(row_id)
    assert row.user_id == ADMIN
    assert row.claim_run_id is None
    assert row.reinstall_done_gen == row.reinstall_requested_gen == 1


async def test_reinstall_of_a_new_resource_creates_its_row_and_hook(store, gitlab):
    result = await reinstall(ADMIN)

    assert result.success
    [hook] = gitlab.hooks[PROJECT]
    await assert_delivery_authenticates(store, hook)


async def test_reinstall_while_a_run_holds_the_claim_is_refused_then_served(
    store, gitlab, add_row, get_row
):
    row_id = await add_row()
    run_id = uuid4()
    await store.claim_rows(run_id, CLAIM_LEASE)

    with pytest.raises(HTTPException) as refused:
        await reinstall(ADMIN)

    assert refused.value.status_code == status.HTTP_409_CONFLICT
    assert gitlab.hooks == {}
    assert (await get_row(row_id)).claim_run_id == run_id

    await store.release_claim(row_id, run_id)
    await run_job()

    [hook] = gitlab.hooks[PROJECT]
    await assert_delivery_authenticates(store, hook)
    assert (await get_row(row_id)).reinstall_done_gen == 1


@pytest.mark.parametrize('route_first', [True, False])
async def test_reinstall_racing_a_scheduled_run_installs_one_hook(
    store, gitlab, add_row, route_first
):
    await add_row()
    arrived = 0
    both_arrived = asyncio.Event()

    async def wait_for_the_other_create():
        # Holds each create until the other caller reaches its own, so both
        # would create unless the claim keeps one of them out.
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both_arrived.set()
        try:
            await asyncio.wait_for(both_arrived.wait(), 0.5)
        except TimeoutError:
            pass

    gitlab.before_create = wait_for_the_other_create
    calls = [reinstall(ADMIN), run_job()]
    if not route_first:
        calls.reverse()
    await asyncio.gather(*calls, return_exceptions=True)
    await run_job()

    [hook] = gitlab.hooks[PROJECT]
    await assert_delivery_authenticates(store, hook)


async def test_run_reaching_a_row_after_its_lease_keeps_the_reinstalled_hook(
    store, gitlab, add_row, get_row, async_session_maker
):
    # A pending request, e.g. from an earlier reinstall that failed.
    row_id = await add_row(
        webhook_exists=True,
        webhook_uuid='uuid-1',
        webhook_secret='secret-1',
        reinstall_requested_gen=1,
    )
    gitlab.add_hook(PROJECT, secret='secret-1', uuid='uuid-1', user=USER)
    run_a = uuid4()
    [stale] = await store.claim_rows(run_a, CLAIM_LEASE)
    # Run A's earlier rows took longer than the lease.
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(GitlabWebhook)
            .where(GitlabWebhook.id == row_id)
            .values(claimed_at=func.now() - CLAIM_LEASE - timedelta(minutes=1))
        )
    assert (await reinstall(ADMIN)).success
    [reinstalled] = gitlab.hooks[PROJECT]

    with pytest.raises(BreakLoopException):
        await install_claimed_webhook(
            gitlab, GitLabResourceType.PROJECT, PROJECT, store, stale, run_a
        )

    assert gitlab.hooks[PROJECT] == [reinstalled]
    await assert_delivery_authenticates(store, reinstalled)
    row = await get_row(row_id)
    assert row.user_id == ADMIN
    assert row.reinstall_done_gen == row.reinstall_requested_gen == 2


async def test_reinstall_by_a_non_admin_is_forbidden(store, gitlab):
    gitlab.is_admin = False

    with pytest.raises(HTTPException) as refused:
        await reinstall(ADMIN)

    assert refused.value.status_code == status.HTTP_403_FORBIDDEN
    assert gitlab.hooks == {}


async def test_reinstall_that_cannot_delete_old_hooks_keeps_them_authenticating(
    store, gitlab, add_row, get_row
):
    row_id = await add_row(
        webhook_exists=True, webhook_uuid='uuid-1', webhook_secret='secret-1'
    )
    old = gitlab.add_hook(PROJECT, secret='secret-1', uuid='uuid-1', user=USER)
    gitlab.delete_status = WebhookStatus.RATE_LIMITED

    with pytest.raises(HTTPException) as refused:
        await reinstall(ADMIN)

    assert refused.value.status_code == status.HTTP_400_BAD_REQUEST
    assert gitlab.hooks[PROJECT] == [old]
    await assert_delivery_authenticates(store, old)
    row = await get_row(row_id)
    assert row.user_id == USER
    assert row.claim_run_id is None


async def test_failed_create_is_an_error_and_releases_the_claim(
    store, gitlab, add_row, get_row
):
    row_id = await add_row()
    gitlab.lose_create_response = True

    with pytest.raises(HTTPException) as failed:
        await reinstall(ADMIN)

    assert failed.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert (await get_row(row_id)).claim_run_id is None
    # GitLab made the hook even though the response was lost, so it must
    # authenticate for the next run to adopt it.
    [hook] = gitlab.hooks[PROJECT]
    await assert_delivery_authenticates(store, hook)


async def test_unexpected_error_releases_the_claim_so_a_retry_is_not_refused(
    store, gitlab, add_row
):
    await add_row()
    gitlab.fail_create = True

    with pytest.raises(HTTPException) as failed:
        await reinstall(ADMIN)

    assert failed.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    gitlab.fail_create = False
    assert (await reinstall(ADMIN)).success


async def test_reinstall_claims_only_its_own_resources_row(
    store, gitlab, add_row, get_row, async_session_maker
):
    # Synced longest ago, so first in line for a claim that ignores the resource.
    other = GitlabWebhook(
        user_id=USER,
        project_id='project-0',
        webhook_exists=False,
        last_synced=datetime(2000, 1, 1),
    )
    async with async_session_maker() as session, session.begin():
        session.add(other)
    row_id = await add_row()

    assert (await reinstall(ADMIN)).success

    row = await get_row(row_id)
    assert row.webhook_exists
    assert row.reinstall_done_gen == 1
    untouched = await get_row(other.id)
    assert untouched.user_id == USER
    assert untouched.webhook_uuid is None


async def test_reinstall_of_a_group_creates_its_row_and_hook(store, gitlab):
    body = ReinstallWebhookRequest(
        resource=ResourceIdentifier(type=GitLabResourceType.GROUP, id='group-1')
    )

    result = await reinstall_gitlab_webhook(body=body, user_id=ADMIN)

    assert result.success
    [hook] = gitlab.hooks['group-1']
    await assert_delivery_authenticates(store, hook)


async def test_reinstall_needs_a_saas_gitlab_service(store, gitlab, monkeypatch):
    monkeypatch.setattr(
        route_module, 'GitLabServiceImpl', lambda external_auth_id: object()
    )

    with pytest.raises(HTTPException) as refused:
        await reinstall(ADMIN)

    assert refused.value.status_code == status.HTTP_400_BAD_REQUEST
