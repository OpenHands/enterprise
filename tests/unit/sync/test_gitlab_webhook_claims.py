"""The GitLab webhook installer's per-run claims, against a real database.

GitLab is the one fake: an in-memory list of hooks per resource, so the tests
can assert how many hooks a resource ends up with and that a delivery signed
with a hook's credentials authenticates.
"""

import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

import storage.gitlab_webhook_store as store_module
import sync.install_gitlab_webhooks as job_module
from integrations.gitlab.webhook_installation import (
    BreakLoopException,
    install_claimed_webhook,
)
from integrations.types import GitLabResourceType
from integrations.utils import GITLAB_WEBHOOK_URL
from storage.gitlab_webhook import GitlabWebhook, WebhookStatus
from storage.gitlab_webhook_store import GitlabWebhookStore
from sync.install_gitlab_webhooks import CLAIM_LEASE, VerifyWebhookStatus
from sync.repair_gitlab_hooks import main as repair_main
from sync.repair_gitlab_hooks import request_repair

USER = 'user-1'


class FakeGitLab:
    def __init__(self):
        self.hooks: dict[str, list[dict]] = {}
        self.resource_exists = True
        self.is_admin = True
        self.delete_status = None
        self.lose_create_response = False
        self.fail_create = False
        self.on_resource_check = None
        self.on_admin_check = None
        self.before_create = None
        self._next_id = 1

    def add_hook(self, resource_id, secret='old-secret', uuid='old-uuid'):
        hook = {
            'id': self._next_id,
            'url': GITLAB_WEBHOOK_URL,
            'secret': secret,
            'uuid': uuid,
        }
        self._next_id += 1
        self.hooks.setdefault(resource_id, []).append(hook)
        return hook

    async def check_resource_exists(self, resource_type, resource_id):
        if self.on_resource_check:
            await self.on_resource_check()
        return self.resource_exists, None

    async def check_user_has_admin_access_to_resource(self, resource_type, resource_id):
        if self.on_admin_check:
            await self.on_admin_check()
        return self.is_admin, None

    async def check_webhook_exists_on_resource(
        self, resource_type, resource_id, webhook_url
    ):
        exists = any(h['url'] == webhook_url for h in self.hooks.get(resource_id, []))
        return exists, None

    async def install_webhook(
        self,
        *,
        resource_type,
        resource_id,
        webhook_name,
        webhook_url,
        webhook_secret,
        webhook_uuid,
        scopes,
    ):
        if self.before_create:
            await self.before_create()
        if self.fail_create:
            raise RuntimeError('pod stopped before the create')
        hook = self.add_hook(resource_id, webhook_secret, webhook_uuid)
        if self.lose_create_response:
            return None, WebhookStatus.INVALID
        return str(hook['id']), None

    async def delete_webhooks_with_url(self, resource_type, resource_id, webhook_url):
        if self.delete_status:
            return 0, self.delete_status
        hooks = self.hooks.get(resource_id, [])
        keep = [h for h in hooks if h['url'] != webhook_url]
        self.hooks[resource_id] = keep
        return len(hooks) - len(keep), None


@pytest.fixture
def gitlab():
    return FakeGitLab()


@pytest.fixture
def store(async_session_maker, gitlab, monkeypatch):
    monkeypatch.setattr(store_module, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(job_module, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(
        job_module, 'GitLabServiceImpl', lambda external_auth_id: gitlab
    )
    return GitlabWebhookStore()


@pytest.fixture
def add_row(async_session_maker):
    async def add(project_id='project-1', **fields):
        fields.setdefault('webhook_exists', False)
        row = GitlabWebhook(user_id=USER, project_id=project_id, **fields)
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


@pytest.fixture
def take_over(async_session_maker, store):
    """Age a row's claim past the lease and claim it for a new run."""

    async def steal(row_id):
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(GitlabWebhook)
                .where(GitlabWebhook.id == row_id)
                .values(claimed_at=func.now() - CLAIM_LEASE - timedelta(minutes=1))
            )
        run_b = uuid4()
        claimed = await store.claim_rows(run_b, CLAIM_LEASE)
        assert [row.id for row in claimed] == [row_id]
        return run_b

    return steal


async def run_job():
    await VerifyWebhookStatus().install_webhooks()


async def assert_delivery_authenticates(store, hook):
    assert await store.get_webhook_secret(hook['uuid'], USER) == hook['secret']


async def test_overlapping_runs_claim_different_rows(store, add_row):
    ids = {await add_row(f'project-{n}') for n in range(6)}

    first, second = await asyncio.gather(
        store.claim_rows(uuid4(), CLAIM_LEASE, limit=4),
        store.claim_rows(uuid4(), CLAIM_LEASE, limit=4),
    )

    first_ids = {row.id for row in first}
    second_ids = {row.id for row in second}
    assert not first_ids & second_ids
    assert first_ids | second_ids == ids


async def test_only_rows_needing_work_are_claimed(store, add_row):
    missing = await add_row('missing')
    await add_row('installed', webhook_exists=True)
    repair = await add_row('repair', webhook_exists=True, reinstall_requested_gen=1)

    claimed = await store.claim_rows(uuid4(), CLAIM_LEASE)

    assert {row.id for row in claimed} == {missing, repair}


async def test_a_claim_is_taken_over_only_after_the_lease(
    store, add_row, get_row, take_over
):
    row_id = await add_row()
    run_a = uuid4()
    await store.claim_rows(run_a, CLAIM_LEASE)
    assert await store.claim_rows(uuid4(), CLAIM_LEASE) == []

    run_b = await take_over(row_id)

    assert not await store.update_claimed(row_id, run_a, webhook_exists=True)
    assert not await store.release_claim(row_id, run_a)
    row = await get_row(row_id)
    assert row.claim_run_id == run_b
    assert row.webhook_exists is False


async def test_lost_create_response_is_adopted_without_a_second_hook(
    store, gitlab, add_row, get_row
):
    row_id = await add_row()
    gitlab.lose_create_response = True
    await run_job()

    row = await get_row(row_id)
    assert row.webhook_exists is False
    assert row.claim_run_id is None
    [hook] = gitlab.hooks['project-1']
    await assert_delivery_authenticates(store, hook)

    gitlab.lose_create_response = False
    await run_job()

    assert gitlab.hooks['project-1'] == [hook]
    assert (await get_row(row_id)).webhook_exists is True


async def test_credentials_are_kept_across_attempts(store, gitlab, add_row, get_row):
    row_id = await add_row()
    gitlab.fail_create = True
    with pytest.raises(RuntimeError):
        await run_job()
    first = await get_row(row_id)
    assert first.webhook_uuid and first.webhook_secret

    gitlab.fail_create = False
    await run_job()

    [hook] = gitlab.hooks['project-1']
    assert (hook['uuid'], hook['secret']) == (first.webhook_uuid, first.webhook_secret)


async def test_run_that_lost_its_claim_creates_no_hook(
    store, gitlab, add_row, get_row, take_over
):
    row_id = await add_row(webhook_uuid='uuid-1', webhook_secret='secret-1')
    run_a = uuid4()
    [row] = await store.claim_rows(run_a, CLAIM_LEASE)

    async def stall_past_lease():
        await take_over(row_id)

    gitlab.on_admin_check = stall_past_lease

    with pytest.raises(BreakLoopException):
        await install_claimed_webhook(
            gitlab, GitLabResourceType.PROJECT, 'project-1', store, row, run_a
        )

    assert gitlab.hooks == {}
    assert (await get_row(row_id)).webhook_exists is False


async def test_run_that_lost_its_claim_writes_no_credentials(
    store, gitlab, add_row, get_row, take_over
):
    row_id = await add_row()
    run_a = uuid4()
    [row] = await store.claim_rows(run_a, CLAIM_LEASE)
    gitlab.on_admin_check = lambda: take_over(row_id)

    with pytest.raises(BreakLoopException):
        await install_claimed_webhook(
            gitlab, GitLabResourceType.PROJECT, 'project-1', store, row, run_a
        )

    stored = await get_row(row_id)
    assert stored.webhook_uuid is None and stored.webhook_secret is None
    assert gitlab.hooks == {}


@pytest.mark.parametrize('failed_check', ['resource', 'admin'])
async def test_run_that_lost_its_claim_does_not_delete_the_row(
    store, gitlab, add_row, get_row, take_over, failed_check
):
    row_id = await add_row()
    run_a = uuid4()
    [row] = await store.claim_rows(run_a, CLAIM_LEASE)
    if failed_check == 'resource':
        gitlab.resource_exists = False
    else:
        gitlab.is_admin = False
    gitlab.on_resource_check = lambda: take_over(row_id)

    with pytest.raises(BreakLoopException):
        await install_claimed_webhook(
            gitlab, GitLabResourceType.PROJECT, 'project-1', store, row, run_a
        )

    assert await get_row(row_id) is not None


@pytest.mark.parametrize('webhook_exists', [True, False])
async def test_repair_replaces_every_hook_with_one(
    store, gitlab, add_row, get_row, webhook_exists
):
    row_id = await add_row(
        webhook_exists=webhook_exists,
        webhook_uuid='uuid-1',
        webhook_secret='secret-1',
    )
    gitlab.add_hook('project-1', secret='stale-a', uuid='stale-a')
    gitlab.add_hook('project-1', secret='stale-b', uuid='stale-b')

    assert await request_repair(GitLabResourceType.PROJECT, 'project-1')
    await run_job()

    [hook] = gitlab.hooks['project-1']
    await assert_delivery_authenticates(store, hook)
    row = await get_row(row_id)
    assert row.webhook_exists is True
    assert row.reinstall_done_gen == row.reinstall_requested_gen == 1


async def test_reinstall_interrupted_after_the_delete_converges(
    store, gitlab, add_row, get_row
):
    row_id = await add_row(webhook_exists=True, reinstall_requested_gen=1)
    gitlab.add_hook('project-1')
    gitlab.fail_create = True
    with pytest.raises(RuntimeError):
        await run_job()
    assert gitlab.hooks['project-1'] == []

    gitlab.fail_create = False
    await run_job()

    assert len(gitlab.hooks['project-1']) == 1
    assert (await get_row(row_id)).reinstall_done_gen == 1


async def test_repair_requested_during_a_reinstall_is_not_lost(
    store, gitlab, add_row, get_row
):
    row_id = await add_row(webhook_exists=True, reinstall_requested_gen=1)
    gitlab.add_hook('project-1')

    async def repair_again():
        gitlab.before_create = None
        await request_repair(GitLabResourceType.PROJECT, 'project-1')

    gitlab.before_create = repair_again
    await run_job()

    row = await get_row(row_id)
    assert (row.reinstall_requested_gen, row.reinstall_done_gen) == (2, 1)

    await run_job()

    [hook] = gitlab.hooks['project-1']
    await assert_delivery_authenticates(store, hook)
    row = await get_row(row_id)
    assert (row.reinstall_requested_gen, row.reinstall_done_gen) == (2, 2)


async def test_repair_request_leaves_a_held_claim_alone(store, add_row, get_row):
    row_id = await add_row()
    run_a = uuid4()
    await store.claim_rows(run_a, CLAIM_LEASE)

    assert await request_repair(GitLabResourceType.PROJECT, 'project-1')

    row = await get_row(row_id)
    assert row.claim_run_id == run_a
    assert row.reinstall_requested_gen == 1


async def test_repair_of_an_unknown_resource_finds_no_row(store):
    assert not await request_repair(GitLabResourceType.GROUP, 'no-such-group')


def test_repair_command_needs_one_resource():
    with pytest.raises(SystemExit):
        repair_main([])
    with pytest.raises(SystemExit):
        repair_main(['--project', '1', '--group', '2'])


async def test_claimed_rows_are_released_after_the_run(store, add_row, get_row):
    row_id = await add_row()

    await run_job()

    row = await get_row(row_id)
    assert row.claim_run_id is None and row.claimed_at is None
    assert row.last_synced is not None


async def test_a_row_locked_by_another_run_is_skipped_not_waited_on(
    store, add_row, async_session_maker
):
    locked = await add_row('locked')
    free = await add_row('free')

    async with async_session_maker() as session, session.begin():
        await session.execute(
            select(GitlabWebhook).where(GitlabWebhook.id == locked).with_for_update()
        )
        claimed = await asyncio.wait_for(store.claim_rows(uuid4(), CLAIM_LEASE), 5)

    assert [row.id for row in claimed] == [free]


async def test_rows_are_claimed_least_recently_synced_first(store, add_row):
    newest = await add_row('newest', last_synced=datetime(2026, 3, 1))
    oldest = await add_row('oldest', last_synced=datetime(2026, 1, 1))
    middle = await add_row('middle', last_synced=datetime(2026, 2, 1))

    claimed = await store.claim_rows(uuid4(), CLAIM_LEASE, limit=2)

    assert [row.id for row in claimed] == [oldest, middle]
    assert newest not in {row.id for row in claimed}


async def test_reinstall_stops_when_the_old_hooks_cannot_be_deleted(
    store, gitlab, add_row, get_row
):
    row_id = await add_row(webhook_exists=True, reinstall_requested_gen=1)
    old = gitlab.add_hook('project-1')
    gitlab.delete_status = WebhookStatus.RATE_LIMITED

    await run_job()

    assert gitlab.hooks['project-1'] == [old]
    assert (await get_row(row_id)).reinstall_done_gen == 0


async def test_repair_request_changes_only_the_request_counter(store, add_row, get_row):
    row_id = await add_row(
        webhook_exists=True, webhook_uuid='uuid-1', webhook_secret='secret-1'
    )
    before = await get_row(row_id)

    assert await request_repair(GitLabResourceType.PROJECT, 'project-1')

    after = await get_row(row_id)
    assert after.reinstall_requested_gen == before.reinstall_requested_gen + 1
    unchanged = ('webhook_exists', 'webhook_uuid', 'webhook_secret', 'claim_run_id')
    assert {f: getattr(after, f) for f in unchanged} == {
        f: getattr(before, f) for f in unchanged
    }
    assert after.reinstall_done_gen == before.reinstall_done_gen
