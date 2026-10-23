"""POST /integration/gitlab/events drops repeat deliveries, against a real database.

Signature checks run against real webhook rows. The one fake is the GitLab
manager the handler passes accepted events to, which would otherwise start
conversations.
"""

import asyncio
import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from starlette.requests import Request

import server.routes.integration.gitlab as route_module
import storage.gitlab_delivery_store as delivery_store_module
import storage.gitlab_webhook_store as webhook_store_module
from server.routes.integration.gitlab import gitlab_events
from storage.gitlab_delivery import GitlabDelivery
from storage.gitlab_delivery_store import (
    PRUNE_BATCH,
    RETENTION,
    GitlabDeliveryStore,
)
from storage.gitlab_webhook import GitlabWebhook

USER = 'user-1'
UUID = 'uuid-1'
SECRET = 'secret-1'


class RecordingManager:
    def __init__(self):
        self.payloads: list[dict] = []

    async def receive_message(self, message):
        await asyncio.sleep(0)
        self.payloads.append(message.message['payload'])


@pytest.fixture
def manager(async_session_maker, monkeypatch):
    monkeypatch.setattr(delivery_store_module, 'a_session_maker', async_session_maker)
    monkeypatch.setattr(webhook_store_module, 'a_session_maker', async_session_maker)
    recording = RecordingManager()
    monkeypatch.setattr(route_module, 'gitlab_manager', recording)
    return recording


@pytest.fixture
async def hook_row(async_session_maker):
    async with async_session_maker() as session, session.begin():
        session.add(
            GitlabWebhook(
                user_id=USER,
                project_id='project-1',
                webhook_exists=True,
                webhook_uuid=UUID,
                webhook_secret=SECRET,
            )
        )


@pytest.fixture
def age_deliveries(async_session_maker):
    async def age(by: timedelta):
        async with async_session_maker() as session, session.begin():
            await session.execute(
                update(GitlabDelivery).values(received_at=func.now() - by)
            )

    return age


def note_event(note_id: int = 1, text: str = '@openhands help') -> bytes:
    return json.dumps(
        {
            'object_kind': 'note',
            'object_attributes': {'id': note_id, 'note': text},
        }
    ).encode()


def deliver(body: bytes, token: str = SECRET):
    async def receive():
        return {'type': 'http.request', 'body': body, 'more_body': False}

    request = Request(
        {'type': 'http', 'method': 'POST', 'path': '/', 'headers': []}, receive
    )
    return gitlab_events(
        request=request,
        x_gitlab_token=token,
        x_openhands_webhook_id=UUID,
        x_openhands_user_id=USER,
    )


async def test_one_event_through_two_hooks_at_once_is_processed_once(manager, hook_row):
    responses = await asyncio.gather(deliver(note_event()), deliver(note_event()))

    assert [r.status_code for r in responses] == [200, 200]
    assert len(manager.payloads) == 1


async def test_a_copy_arriving_long_after_the_first_is_still_dropped(
    manager, hook_row, age_deliveries
):
    await deliver(note_event())
    await age_deliveries(timedelta(hours=1))

    response = await deliver(note_event())

    assert json.loads(response.body)['message'] == 'Duplicate GitLab event ignored.'
    assert len(manager.payloads) == 1


async def test_distinct_events_on_the_same_object_are_both_processed(manager, hook_row):
    await deliver(note_event(note_id=7, text='first'))
    await deliver(note_event(note_id=7, text='second'))

    assert [p['object_attributes']['note'] for p in manager.payloads] == [
        'first',
        'second',
    ]


async def test_a_delivery_that_fails_authentication_is_not_recorded(manager, hook_row):
    rejected = await deliver(note_event(), token='wrong-secret')
    await deliver(note_event())

    assert rejected.status_code != 200
    assert len(manager.payloads) == 1


async def test_record_is_true_only_for_the_first_of_concurrent_copies(manager):
    store = GitlabDeliveryStore()

    results = await asyncio.gather(*(store.record(b'same body') for _ in range(5)))

    assert sorted(results) == [False, False, False, False, True]


async def test_an_entry_past_retention_is_pruned_and_its_body_counts_as_new(
    manager, age_deliveries
):
    store = GitlabDeliveryStore()
    await store.record(b'old')
    await age_deliveries(RETENTION + timedelta(minutes=1))

    assert await store.record(b'old')
    assert not await store.record(b'old')


async def test_pruning_deletes_at_most_one_batch_per_record(
    manager, age_deliveries, async_session_maker
):
    store = GitlabDeliveryStore()
    for n in range(PRUNE_BATCH + 5):
        await store.record(f'old-{n}'.encode())
    await age_deliveries(RETENTION + timedelta(minutes=1))

    await store.record(b'new')

    async with async_session_maker() as session:
        left = await session.scalar(select(func.count()).select_from(GitlabDelivery))
    assert left == 5 + 1
