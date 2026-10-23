"""The installer's repair sweep, against a real database.

GitLab is the one fake (``tests.unit.gitlab_fakes``).
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, update

import sync.install_gitlab_webhooks as job_module
from integrations.gitlab.hook_identity import hook_identity, hook_url
from storage.gitlab_webhook import GitlabWebhook, WebhookStatus
from sync.install_gitlab_webhooks import (
    CLAIM_LEASE,
    REPAIR_INTERVAL,
    VerifyWebhookStatus,
)
from tests.unit.gitlab_fakes import assert_delivery_authenticates

PROJECT = 'project-1'


async def run_job():
    await VerifyWebhookStatus().install_webhooks()


@pytest.fixture
def age_repair(async_session_maker):
    async def age(row_id, by):
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(GitlabWebhook)
                .where(GitlabWebhook.id == row_id)
                .values(last_repaired_at=func.now() - by)
            )

    return age


@pytest.fixture
def add_installed_row(add_row, add_intent, gitlab):
    """A row with a committed identity, and that identity's hook on GitLab."""

    async def add(project_id=PROJECT, **fields):
        hook_uuid = f'{project_id}-current'
        row_id = await add_row(
            project_id,
            webhook_exists=True,
            webhook_uuid=hook_uuid,
            webhook_secret='secret-current',
            **fields,
        )
        await add_intent(row_id, hook_uuid, 'committed')
        hook = gitlab.add_hook(
            project_id, 'secret-current', hook_uuid, url=hook_url(hook_uuid)
        )
        return row_id, hook

    return add


async def test_hooks_with_a_stale_identity_are_deleted_and_others_kept(
    store, gitlab, add_installed_row, add_intent, get_row
):
    row_id, current = await add_installed_row()
    await add_intent(row_id, 'uuid-old', 'replaced')
    gitlab.add_hook(PROJECT, uuid='uuid-old', url=hook_url('uuid-old'))
    gitlab.add_hook(PROJECT, uuid='unknown', url=hook_url('unknown'))
    someone_elses = gitlab.add_hook(PROJECT, url='https://someone-else/hook')

    await run_job()

    assert gitlab.hooks[PROJECT] == [current, someone_elses]
    await assert_delivery_authenticates(store, current)
    assert (await get_row(row_id)).last_repaired_at is not None


async def test_an_orphan_from_a_crashed_run_is_abandoned_and_deleted(
    store, gitlab, add_installed_row, add_intent
):
    row_id, current = await add_installed_row()
    await add_intent(row_id, 'uuid-orphan', 'open', run_id=uuid4())
    gitlab.add_hook(PROJECT, uuid='uuid-orphan', url=hook_url('uuid-orphan'))

    await run_job()

    assert gitlab.hooks[PROJECT] == [current]
    assert (await store.get_intent_states(row_id))['uuid-orphan'] == 'abandoned'


async def test_legacy_hooks_are_converted_to_one_identity_hook(
    store, gitlab, add_row, add_intent
):
    row_id = await add_row(
        webhook_exists=True, webhook_uuid='uuid-1', webhook_secret='secret-1'
    )
    await add_intent(row_id, 'uuid-1', 'committed')
    legacy = gitlab.add_hook(PROJECT, 'secret-1', 'uuid-1')
    gitlab.add_hook(PROJECT, 'secret-0', 'uuid-0')
    await assert_delivery_authenticates(store, legacy)

    await run_job()

    [hook] = gitlab.hooks[PROJECT]
    assert hook_identity(hook['url']) == hook['uuid']
    await assert_delivery_authenticates(store, hook)
    assert await store.get_intent_states(row_id) == {
        'uuid-1': 'replaced',
        hook['uuid']: 'committed',
    }


async def test_a_failed_conversion_keeps_the_legacy_hook_working(
    store, gitlab, add_row, add_intent
):
    row_id = await add_row(
        webhook_exists=True, webhook_uuid='uuid-1', webhook_secret='secret-1'
    )
    await add_intent(row_id, 'uuid-1', 'committed')
    legacy = gitlab.add_hook(PROJECT, 'secret-1', 'uuid-1')
    gitlab.lose_create_response = True

    await run_job()

    assert gitlab.hooks[PROJECT][0] == legacy
    await assert_delivery_authenticates(store, legacy)


async def test_a_row_whose_hook_is_gone_is_handed_back_to_the_installer(
    store, gitlab, add_installed_row, get_row
):
    row_id, _ = await add_installed_row()
    gitlab.hooks[PROJECT] = []

    await run_job()
    assert (await get_row(row_id)).webhook_exists is False

    await run_job()
    [hook] = gitlab.hooks[PROJECT]
    await assert_delivery_authenticates(store, hook)


async def test_a_row_is_swept_at_most_once_per_interval(
    store, gitlab, add_installed_row, get_row, age_repair
):
    row_id, current = await add_installed_row()
    await run_job()
    stale = gitlab.add_hook(PROJECT, uuid='uuid-old', url=hook_url('uuid-old'))

    await run_job()
    assert gitlab.hooks[PROJECT] == [current, stale]

    await age_repair(row_id, REPAIR_INTERVAL + timedelta(minutes=1))
    await run_job()
    assert gitlab.hooks[PROJECT] == [current]


async def test_never_swept_rows_go_first_and_a_run_sweeps_one_batch(
    store, gitlab, add_installed_row, monkeypatch
):
    monkeypatch.setattr(job_module, 'REPAIR_BATCH', 1)
    swept_before = datetime.now(UTC) - REPAIR_INTERVAL - timedelta(hours=1)
    await add_installed_row('swept-before', last_repaired_at=swept_before)
    await add_installed_row('never-swept')
    gitlab.add_hook('swept-before', url=hook_url('stale-1'))
    gitlab.add_hook('never-swept', url=hook_url('stale-2'))

    await run_job()

    assert len(gitlab.hooks['never-swept']) == 1
    assert len(gitlab.hooks['swept-before']) == 2


async def test_a_row_another_run_holds_is_not_swept(store, gitlab, add_installed_row):
    await add_installed_row()
    stale = gitlab.add_hook(PROJECT, url=hook_url('uuid-old'))
    assert await store.claim_rows_for_repair(
        uuid4(), CLAIM_LEASE, REPAIR_INTERVAL, limit=1
    )

    await run_job()

    assert stale in gitlab.hooks[PROJECT]


async def test_open_intents_are_abandoned_only_by_the_claim_holder(
    store, add_installed_row, add_intent
):
    row_id, _ = await add_installed_row()
    await add_intent(row_id, 'uuid-open', 'open', run_id=uuid4())
    holder = uuid4()
    await store.claim_rows_for_repair(holder, CLAIM_LEASE, REPAIR_INTERVAL, limit=1)

    assert await store.abandon_open_intents(row_id, uuid4()) == 0
    assert await store.abandon_open_intents(row_id, holder) == 1


async def test_a_sweep_that_cannot_delete_stops_and_releases_its_claim(
    store, gitlab, add_installed_row, get_row
):
    row_id, current = await add_installed_row()
    stale = gitlab.add_hook(PROJECT, url=hook_url('uuid-old'))
    gitlab.delete_status = WebhookStatus.RATE_LIMITED

    await run_job()

    assert gitlab.hooks[PROJECT] == [current, stale]
    assert (await get_row(row_id)).claim_run_id is None


async def test_a_row_awaiting_a_reinstall_is_left_to_the_installer(
    store, add_installed_row
):
    await add_installed_row(reinstall_requested_gen=1)

    assert (
        await store.claim_rows_for_repair(
            uuid4(), CLAIM_LEASE, REPAIR_INTERVAL, limit=10
        )
        == []
    )
