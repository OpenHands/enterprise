"""Route tests for email matching in ``_resolve_or_create_user`` (OHE-3723).

Covers:
- An unlinked subject is linked to the existing user with the same email when
  the provider has ``allow_match_by_email`` set.
- ``email_verified`` from the provider does not gate the match.
- A provider without ``allow_match_by_email`` never matches by email.
- Duplicate emails produce a 409 (no auto-link).
- ``AuthProvider.allow_registration=False`` gates only the final "mint a
  brand-new user" fallback.

The resolver is tested directly (it's a plain async function) so we don't
need the full callback HTTP dance. Real-database coverage of the same paths
lives in ``tests/unit/server/auth/test_provider_identity_email_claims.py``.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from server.routes import oauth_v2
from server.routes.oauth_v2 import _resolve_or_create_user
from storage.auth_provider import AuthProvider


def _fake_provider(
    provider_id: int = 1,
    allow_registration: bool = True,
    allow_match_by_email: bool = True,
) -> AuthProvider:
    p = MagicMock()
    p.id = provider_id
    p.allow_registration = allow_registration
    p.allow_match_by_email = allow_match_by_email
    return p


def _fake_user(user_id, email=None):
    u = MagicMock()
    u.id = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    u.email = email
    return u


# ── _resolve_or_create_user email-match branch ───────────────────────────


@pytest.mark.asyncio
async def test_email_match_links_to_existing_user():
    provider = _fake_provider()
    existing_user = _fake_user(uuid.uuid4(), email='alice@example.com')

    with (
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.UserStore,
            'count_users_with_email',
            new=AsyncMock(return_value=1),
        ),
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_email',
            new=AsyncMock(return_value=existing_user),
        ) as by_email,
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'link',
            new=AsyncMock(),
        ) as link,
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
    by_email.assert_awaited_once_with('alice@example.com')
    link.assert_awaited_once()
    assert link.call_args.kwargs['user_id'] == existing_user.id
    assert link.call_args.kwargs['external_subject_id'] == 'new-idp-sub'
    create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('claims', [{}, {'email_verified': False}])
async def test_email_match_ignores_email_verified(claims):
    provider = _fake_provider()
    existing_user = _fake_user(uuid.uuid4(), email='alice@example.com')

    with (
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.UserStore,
            'count_users_with_email',
            new=AsyncMock(return_value=1),
        ),
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_email',
            new=AsyncMock(return_value=existing_user),
        ),
        patch.object(oauth_v2.AuthProviderUserStore, 'link', new=AsyncMock()),
        patch.object(oauth_v2.UserStore, 'create_user', new=AsyncMock()) as create,
    ):
        result = await _resolve_or_create_user(
            provider,
            {'sub': 'new-idp-sub', 'email': 'alice@example.com', **claims},
        )

    assert result == str(existing_user.id)
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_email_match_duplicate_returns_409():
    provider = _fake_provider()

    with (
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.UserStore,
            'count_users_with_email',
            new=AsyncMock(return_value=2),
        ),
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_email',
            new=AsyncMock(),
        ) as by_email,
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
    by_email.assert_not_awaited()
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_email_match_no_user_falls_through_to_create():
    provider = _fake_provider()
    new_user = _fake_user(uuid.uuid4(), email='alice@example.com')

    with (
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.UserStore,
            'count_users_with_email',
            new=AsyncMock(return_value=0),
        ),
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_email',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.UserStore,
            'create_user',
            new=AsyncMock(return_value=new_user),
        ) as create,
        patch.object(oauth_v2.AuthProviderUserStore, 'link', new=AsyncMock()) as link,
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
    # 'new-idp-sub' is not a UUID -- UserStore.create_user requires one (it
    # derives Org.id from it too), so the external subject must never be
    # passed through as the internal User.id.
    created_user_id = create.await_args.args[0]
    uuid.UUID(created_user_id)
    assert created_user_id != 'new-idp-sub'


@pytest.mark.asyncio
async def test_provider_without_allow_match_by_email_never_matches():
    provider = _fake_provider(allow_match_by_email=False)
    existing_user = _fake_user(uuid.uuid4(), email='alice@example.com')
    new_user = _fake_user(uuid.uuid4(), email='alice@example.com')

    with (
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.UserStore,
            'count_users_with_email',
            new=AsyncMock(return_value=1),
        ) as count,
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_email',
            new=AsyncMock(return_value=existing_user),
        ) as by_email,
        patch.object(
            oauth_v2.UserStore,
            'create_user',
            new=AsyncMock(return_value=new_user),
        ) as create,
        patch.object(oauth_v2.AuthProviderUserStore, 'link', new=AsyncMock()) as link,
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
    count.assert_not_awaited()
    by_email.assert_not_awaited()
    create.assert_awaited_once()
    assert link.call_args.kwargs['user_id'] == new_user.id


@pytest.mark.asyncio
async def test_sub_lookup_hit_skips_email_match_entirely():
    provider = _fake_provider()
    link_row = MagicMock()
    link_row.user_id = uuid.uuid4()

    with (
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=link_row),
        ),
        patch.object(
            oauth_v2.UserStore,
            'count_users_with_email',
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


# ── allow_registration=False ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_allow_registration_false_blocks_brand_new_user_with_403():
    provider = _fake_provider(allow_registration=False)

    with (
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.UserStore,
            'count_users_with_email',
            new=AsyncMock(return_value=0),
        ),
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_email',
            new=AsyncMock(return_value=None),
        ),
        patch.object(oauth_v2.UserStore, 'create_user', new=AsyncMock()) as create,
    ):
        with pytest.raises(HTTPException) as exc:
            await _resolve_or_create_user(
                provider,
                {
                    'sub': 'new-idp-sub',
                    'email': 'alice@example.com',
                    'email_verified': True,
                },
            )

    assert exc.value.status_code == 403
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_allow_registration_false_still_allows_existing_linked_user():
    """The gate only blocks the ``create_user`` fallback -- an existing
    ``auth_provider_users`` link must still sign in through the row."""
    provider = _fake_provider(allow_registration=False)
    link_row = MagicMock()
    link_row.user_id = uuid.uuid4()

    with (
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=link_row),
        ),
        patch.object(oauth_v2.UserStore, 'create_user', new=AsyncMock()) as create,
    ):
        result = await _resolve_or_create_user(
            provider,
            {'sub': 'known-sub', 'email': 'alice@example.com'},
        )

    assert result == str(link_row.user_id)
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_allow_registration_false_still_allows_email_match():
    """The gate only blocks the ``create_user`` fallback -- the email-match
    path resolves to an existing user and must not be gated."""
    provider = _fake_provider(allow_registration=False)
    existing_user = _fake_user(uuid.uuid4(), email='alice@example.com')

    with (
        patch.object(
            oauth_v2.AuthProviderUserStore,
            'get',
            new=AsyncMock(return_value=None),
        ),
        patch.object(
            oauth_v2.UserStore,
            'count_users_with_email',
            new=AsyncMock(return_value=1),
        ),
        patch.object(
            oauth_v2.UserStore,
            'get_user_by_email',
            new=AsyncMock(return_value=existing_user),
        ),
        patch.object(oauth_v2.AuthProviderUserStore, 'link', new=AsyncMock()),
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
    create.assert_not_awaited()
