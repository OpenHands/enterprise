"""Email matching on the OAuth v2 / SAML login path (OHE-3723).

A login through a second IdP should land on the account created by the first
one when both assert the same email and the account has opted in via
``allow_match_by_email``. The IdP is trusted: ``email_verified`` does not gate
the match, it is only recorded on newly created users.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from openhands.app_server.integrations.service_types import ProviderType
from server.auth.provider_identity import is_true_claim, resolve_or_create_user
from storage.role import Role
from storage.user import User


async def _user_count(async_session_maker) -> int:
    async with async_session_maker() as session:
        return await session.scalar(select(func.count()).select_from(User))


async def _get_user(async_session_maker, user_id: str) -> User:
    async with async_session_maker() as session:
        return await session.get(User, user_id)


@pytest.fixture
async def idps(create_git_provider, app_db_session, async_session_maker):
    """Two login IdPs, with new-user provisioning not reaching for LiteLLM."""
    async with async_session_maker() as session:
        session.add(Role(name='owner', rank=10))
        await session.commit()
    first = await create_git_provider(ProviderType.ENTERPRISE_SSO, is_idp=True)
    second = await create_git_provider(ProviderType.ENTERPRISE_SSO, is_idp=True)
    with patch('storage.lite_llm_manager.ENABLE_LEGACY_LITELLM', False):
        yield first, second


@pytest.mark.parametrize(
    'value, expected',
    [
        (True, True),
        ('true', True),
        ('TRUE', True),
        (False, False),
        ('false', False),
        (None, False),
        (1, False),
        ('yes', False),
    ],
)
def test_is_true_claim(value, expected):
    assert is_true_claim(value) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'claims',
    [{}, {'email_verified': False}, {'email_verified': 'false'}],
    ids=['absent', 'false', 'string-false'],
)
async def test_unverified_email_matches_opted_in_user(
    idps, async_session_maker, claims
):
    first, second = idps
    owner_id = await resolve_or_create_user(
        first, {'sub': 'a-1', 'email': 'alice@example.com', **claims}
    )

    matched = await resolve_or_create_user(
        second, {'sub': 'b-1', 'email': 'alice@example.com', **claims}
    )

    assert matched == owner_id
    assert await _user_count(async_session_maker) == 1


@pytest.mark.asyncio
async def test_mixed_case_email_is_stored_lowercased_and_matches_later(
    idps, async_session_maker
):
    first, second = idps
    user_id = await resolve_or_create_user(
        first, {'sub': 'a-1', 'email': ' Bob@Example.COM ', 'email_verified': True}
    )
    assert (await _get_user(async_session_maker, user_id)).email == 'bob@example.com'

    matched = await resolve_or_create_user(
        second, {'sub': 'b-1', 'email': 'bob@example.com'}
    )

    assert matched == user_id
    assert await _user_count(async_session_maker) == 1


@pytest.mark.asyncio
async def test_string_true_email_verified_is_stored_as_boolean(
    idps, async_session_maker
):
    first, _ = idps
    user_id = await resolve_or_create_user(
        first, {'sub': 'a-1', 'email': 'carol@example.com', 'email_verified': 'true'}
    )

    assert (await _get_user(async_session_maker, user_id)).email_verified is True


@pytest.mark.asyncio
async def test_user_without_allow_match_by_email_is_not_matched(
    idps, async_session_maker
):
    first, second = idps
    first.allow_match_by_email = False
    owner_id = await resolve_or_create_user(
        first, {'sub': 'a-1', 'email': 'dave@example.com', 'email_verified': True}
    )

    other_id = await resolve_or_create_user(
        second, {'sub': 'b-1', 'email': 'dave@example.com', 'email_verified': True}
    )

    assert other_id != owner_id
