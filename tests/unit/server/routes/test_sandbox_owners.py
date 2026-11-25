"""The sandbox owner lookup runs against a real database through the real permission check."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from openhands.app_server.app_conversation.sql_app_conversation_info_service import (
    StoredConversationMetadata,
)
from openhands.app_server.sandbox.sandbox_store import StoredSandbox
from openhands.app_server.user_auth import get_user_id
from server.routes.sandbox_owners import (
    CONVERSATIONS_PER_ENTRY,
    MAX_SANDBOX_IDS,
    sandbox_owner_admin_router,
)
from storage.stored_conversation_metadata_saas import StoredConversationMetadataSaas

URL = '/api/admin/sandboxes/owners'
NOW = datetime.now(UTC)


@pytest.fixture
def client(async_session_maker):
    app = FastAPI()
    app.include_router(sandbox_owner_admin_router)
    app.dependency_overrides[get_user_id] = lambda: str(uuid.uuid4())
    with patch('server.routes.sandbox_owners.a_session_maker', async_session_maker):
        yield AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


@pytest.fixture
def as_super_admin():
    superadmin = MagicMock()
    superadmin.name = 'admin'
    with (
        patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=None),
        ),
        patch(
            'server.auth.authorization.get_user_super_role',
            AsyncMock(return_value=superadmin),
        ),
    ):
        yield


@pytest.fixture
def add_rows(async_session_maker):
    async def _add(*rows):
        async with async_session_maker() as session:
            session.add_all(rows)
            await session.commit()

    return _add


def _sandbox(sandbox_id, user, org, age=timedelta(0)):
    return StoredSandbox(
        id=sandbox_id,
        created_by_user_id=str(user.id),
        credential_org_id=org.id,
        sandbox_spec_id='spec',
        created_at=NOW - age,
    )


def _conversation(conversation_id, sandbox_id, user, org, age=timedelta(0), **meta):
    return [
        StoredConversationMetadata(
            conversation_id=conversation_id,
            sandbox_id=sandbox_id,
            created_at=NOW - age,
            **meta,
        ),
        StoredConversationMetadataSaas(
            conversation_id=conversation_id, user_id=user.id, org_id=org.id
        ),
    ]


async def test_non_super_admins_are_forbidden(client):
    with patch(
        'server.auth.authorization.get_user_org_role', AsyncMock(return_value=None)
    ):
        resp = await client.post(URL, json={'sandbox_ids': ['sb']})

    assert resp.status_code == 403


@pytest.mark.parametrize('ids', [[], [str(i) for i in range(MAX_SANDBOX_IDS + 1)]])
async def test_rejects_an_empty_or_oversized_batch(client, as_super_admin, ids):
    resp = await client.post(URL, json={'sandbox_ids': ids})

    assert resp.status_code == 422


async def test_resolves_owner_activity_and_unknown_ids(
    client, as_super_admin, add_rows, create_org, create_user
):
    free_org = create_org()
    paid_org = create_org(byor_export_enabled=True)
    signed_up = datetime(2026, 11, 20, 8, 30)
    free_user = create_user(
        current_org_id=free_org.id,
        email='free@example.com',
        email_verified=False,
        first_login_at=signed_up,
    )
    paid_user = create_user(current_org_id=paid_org.id, email='paid@example.com')
    await add_rows(
        _sandbox('recent', free_user, free_org),
        _sandbox('older', free_user, free_org, age=timedelta(days=3)),
        _sandbox('ancient', free_user, free_org, age=timedelta(days=30)),
        _sandbox('paid', paid_user, paid_org),
        *_conversation('c-live', 'recent', free_user, free_org, title='fix bug'),
        *_conversation(
            'c-hidden',
            'recent',
            free_user,
            free_org,
            age=timedelta(hours=1),
            title='hide me',
            deleted_at=NOW,
        ),
        *_conversation('c-other', 'older', free_user, free_org, age=timedelta(days=3)),
    )

    resp = await client.post(
        URL, json={'sandbox_ids': ['recent', 'paid', 'missing', 'missing']}
    )

    body = resp.json()
    assert resp.status_code == 200
    assert body['unknown_sandbox_ids'] == ['missing']
    by_id = {s['sandbox_id']: s for s in body['sandboxes']}
    assert set(by_id) == {'recent', 'paid'}
    assert by_id['recent']['byor_export_enabled'] is False
    assert by_id['paid']['byor_export_enabled'] is True
    assert by_id['recent']['conversation_count'] == 2
    assert [c['id'] for c in by_id['recent']['recent_conversations']] == [
        'c-live',
        'c-hidden',
    ]
    assert by_id['recent']['recent_conversations'][1]['deleted_at'] is not None
    owner = body['owners'][str(free_user.id)]
    assert owner['email'] == 'free@example.com'
    assert owner['email_verified'] is False
    assert owner['first_login_at'].startswith('2026-11-20T08:30:00')
    assert owner['is_disabled'] is False
    assert (owner['sandboxes_started_24h'], owner['sandboxes_started_7d']) == (1, 2)
    assert owner['conversation_count'] == 3
    assert {c['sandbox_id'] for c in owner['recent_conversations']} == {
        'recent',
        'older',
    }
    assert body['owners'][str(paid_user.id)]['conversation_count'] == 0


async def test_conversation_lists_are_capped_but_counts_are_not(
    client, as_super_admin, add_rows, create_org, create_user
):
    org = create_org()
    user = create_user(current_org_id=org.id)
    total = CONVERSATIONS_PER_ENTRY + 3
    await add_rows(
        _sandbox('busy', user, org),
        *[
            row
            for n in range(total)
            for row in _conversation(
                f'c-{n}', 'busy', user, org, age=timedelta(minutes=n)
            )
        ],
    )

    body = (await client.post(URL, json={'sandbox_ids': ['busy']})).json()

    [sandbox] = body['sandboxes']
    assert sandbox['conversation_count'] == total
    assert len(sandbox['recent_conversations']) == CONVERSATIONS_PER_ENTRY
    assert sandbox['recent_conversations'][0]['id'] == 'c-0'
    owner = body['owners'][str(user.id)]
    assert owner['conversation_count'] == total
    assert len(owner['recent_conversations']) == CONVERSATIONS_PER_ENTRY
