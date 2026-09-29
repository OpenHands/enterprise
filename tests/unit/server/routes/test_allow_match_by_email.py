"""Route tests for the ``allow_match_by_email`` IDP swap-over flow (ALL-5978).

Covers:
- ``_resolve_or_create_user`` email-match branch: opted-in unique hit links
  to the existing user and self-clears the flag.
- ``email_verified`` is required from the new IDP before honoring the match.
- Duplicate opted-in emails produce a 409 (no auto-link).
- The super-admin bulk-set endpoint ``PUT /api/admin/super-admins/allow-match-by-email``
  delegates to ``UserStore.bulk_set_allow_match_by_email`` and reports duplicates.

The resolver is tested directly (it's a plain async function) so we don't
need the full callback HTTP dance; the admin endpoint is tested through the
FastAPI router with ``require_permission`` faked the same way
``test_super_admins.py`` fakes it.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from openhands.app_server.user_auth import get_user_id
from server.routes import oauth_v2
from server.routes.oauth_v2 import _resolve_or_create_user
from server.routes.super_admins import super_admin_router
from storage.oauth_provider import OAuthProvider

CALLER_USER_ID = str(uuid.uuid4())


def _fake_provider(provider_id: int = 1) -> OAuthProvider:
    p = MagicMock()
    p.id = provider_id
    return p


def _fake_user(user_id, email=None):
    u = MagicMock()
    u.id = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    u.email = email
    return u


# ── _resolve_or_create_user email-match branch ───────────────────────────


@pytest.mark.asyncio
async def test_email_match_links_to_existing_user_and_clears_flag():
    provider = _fake_provider()
    existing_user = _fake_user(uuid.uuid4(), email='alice@example.com')

    with (
        patch.object(
            oauth_v2.OAuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.UserStore,
            'count_opted_in_users_with_email',
            new=AsyncMock(return_value=1),
        ),
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_email_opted_in',
            new=AsyncMock(return_value=existing_user),
        ) as opted_in,
        patch.object(
            oauth_v2.OAuthProviderUserStore,
            'link',
            new=AsyncMock(),
        ) as link,
        patch.object(
            oauth_v2.UserStore,
            'clear_allow_match_by_email',
            new=AsyncMock(),
        ) as clear,
        patch.object(oauth_v2.UserStore, 'create_user', new=AsyncMock()) as create,
    ):
        result = await _resolve_or_create_user(
            provider,
            {
                'sub': 'new-idp-sub',
                'email': 'alice@example.com',
                'email_verified': True,
            },
        )

    assert result == str(existing_user.id)
    opted_in.assert_awaited_once_with('alice@example.com')
    link.assert_awaited_once()
    assert link.call_args.kwargs['user_id'] == existing_user.id
    assert link.call_args.kwargs['external_subject_id'] == 'new-idp-sub'
    clear.assert_awaited_once_with(str(existing_user.id))
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_email_match_skipped_when_email_not_verified():
    provider = _fake_provider()
    new_user = _fake_user(uuid.uuid4(), email='alice@example.com')

    with (
        patch.object(
            oauth_v2.OAuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.UserStore,
            'count_opted_in_users_with_email',
            new=AsyncMock(return_value=0),
        ) as count,
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_email_opted_in',
            new=AsyncMock(),
        ) as opted_in,
        patch.object(
            oauth_v2.UserStore,
            'create_user',
            new=AsyncMock(return_value=new_user),
        ),
        patch.object(oauth_v2.OAuthProviderUserStore, 'link', new=AsyncMock()),
    ):
        result = await _resolve_or_create_user(
            provider,
            {
                'sub': 'new-idp-sub',
                'email': 'alice@example.com',
                'email_verified': False,
            },
        )

    # Falls through to create_user; email-match never attempted.
    count.assert_not_awaited()
    opted_in.assert_not_awaited()
    assert result == str(new_user.id)


@pytest.mark.asyncio
async def test_email_match_duplicate_returns_409():
    provider = _fake_provider()

    with (
        patch.object(
            oauth_v2.OAuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.UserStore,
            'count_opted_in_users_with_email',
            new=AsyncMock(return_value=2),
        ),
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_email_opted_in',
            new=AsyncMock(),
        ) as opted_in,
        patch.object(oauth_v2.UserStore, 'create_user', new=AsyncMock()) as create,
    ):
        with pytest.raises(HTTPException) as exc:
            await _resolve_or_create_user(
                provider,
                {
                    'sub': 'new-idp-sub',
                    'email': 'shared@example.com',
                    'email_verified': True,
                },
            )

    assert exc.value.status_code == 409
    opted_in.assert_not_awaited()
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_email_match_no_opted_in_user_falls_through_to_create():
    provider = _fake_provider()
    new_user = _fake_user(uuid.uuid4(), email='alice@example.com')

    with (
        patch.object(
            oauth_v2.OAuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.UserStore,
            'count_opted_in_users_with_email',
            new=AsyncMock(return_value=0),
        ),
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_email_opted_in',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.UserStore,
            'create_user',
            new=AsyncMock(return_value=new_user),
        ),
        patch.object(oauth_v2.OAuthProviderUserStore, 'link', new=AsyncMock()) as link,
    ):
        result = await _resolve_or_create_user(
            provider,
            {
                'sub': 'new-idp-sub',
                'email': 'alice@example.com',
                'email_verified': True,
            },
        )

    assert result == str(new_user.id)
    # New user link, not the email-match link path.
    link.assert_awaited_once()
    assert link.call_args.kwargs['user_id'] == new_user.id


@pytest.mark.asyncio
async def test_sub_lookup_hit_skips_email_match_entirely():
    provider = _fake_provider()
    link_row = MagicMock()
    link_row.user_id = uuid.uuid4()

    with (
        patch.object(
            oauth_v2.OAuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=link_row),
        ),
        patch.object(
            oauth_v2.UserStore,
            'count_opted_in_users_with_email',
            new=AsyncMock(),
        ) as count,
    ):
        result = await _resolve_or_create_user(
            provider,
            {
                'sub': 'known-sub',
                'email': 'alice@example.com',
                'email_verified': True,
            },
        )

    assert result == str(link_row.user_id)
    count.assert_not_awaited()


# ── super-admin bulk-set endpoint ────────────────────────────────────────


@pytest.fixture
def mock_app():
    app = FastAPI()
    app.include_router(super_admin_router)
    app.dependency_overrides[get_user_id] = lambda: CALLER_USER_ID
    return app


@pytest.fixture
def grant_manage_super_admins():
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


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


@pytest.mark.asyncio
async def test_bulk_set_allow_match_by_email_success(
    mock_app, grant_manage_super_admins
):
    with patch(
        'server.routes.super_admins.UserStore.bulk_set_allow_match_by_email',
        AsyncMock(
            return_value={
                'updated': 5,
                'duplicate_emails': ['dup@example.com'],
            }
        ),
    ) as bulk:
        async with _client(mock_app) as client:
            resp = await client.put(
                '/api/admin/super-admins/allow-match-by-email?value=true'
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body['updated'] == 5
    assert body['duplicate_emails'] == ['dup@example.com']
    assert body['value'] is True
    bulk.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_bulk_set_allow_match_by_email_default_true(
    mock_app, grant_manage_super_admins
):
    with patch(
        'server.routes.super_admins.UserStore.bulk_set_allow_match_by_email',
        AsyncMock(return_value={'updated': 0, 'duplicate_emails': []}),
    ) as bulk:
        async with _client(mock_app) as client:
            resp = await client.put('/api/admin/super-admins/allow-match-by-email')

    assert resp.status_code == 200
    bulk.assert_awaited_once_with(True)


@pytest.mark.asyncio
async def test_bulk_set_allow_match_by_email_disable(
    mock_app, grant_manage_super_admins
):
    with patch(
        'server.routes.super_admins.UserStore.bulk_set_allow_match_by_email',
        AsyncMock(return_value={'updated': 3, 'duplicate_emails': []}),
    ) as bulk:
        async with _client(mock_app) as client:
            resp = await client.put(
                '/api/admin/super-admins/allow-match-by-email?value=false'
            )

    assert resp.status_code == 200
    assert resp.json()['value'] is False
    bulk.assert_awaited_once_with(False)


@pytest.mark.asyncio
async def test_bulk_set_allow_match_by_email_requires_super_admin(mock_app):
    """Without the super-admin role the endpoint must 403."""
    with (
        patch('server.routes.super_admins.USER_PROVISIONING_ENABLED', False),
        patch(
            'server.auth.authorization.get_user_org_role',
            AsyncMock(return_value=None),
        ),
        patch(
            'server.auth.authorization.get_user_super_role',
            AsyncMock(return_value=None),
        ),
    ):
        async with _client(mock_app) as client:
            resp = await client.put('/api/admin/super-admins/allow-match-by-email')
    assert resp.status_code == 403
