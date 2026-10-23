"""``email`` / ``email_verified`` handling on the OAuth v2 login path (OHE-3723).

A login through a second IdP should land on the account created by the first
one when both assert the same verified email; anything that drops or misreads
``email_verified`` (or the email's case) mints a duplicate account instead.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import jwt
import pytest
from sqlalchemy import func, select

from openhands.app_server.integrations.service_types import ProviderType
from server.auth.provider_identity import is_true_claim, resolve_or_create_user
from server.routes.oauth_v2 import _merge_id_token_claims
from storage.auth_provider_details import OAuthProviderDetails
from storage.role import Role
from storage.user import User


def _id_token(**claims) -> str:
    return jwt.encode(claims, 'not-checked', algorithm='HS256')


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
async def test_mixed_case_email_is_stored_lowercased_and_matches_later(
    idps, async_session_maker
):
    first, second = idps
    user_id = await resolve_or_create_user(
        first,
        {'sub': 'a-1', 'email': ' Alice@Example.COM ', 'email_verified': True},
    )
    user = await _get_user(async_session_maker, user_id)
    assert user.email == 'alice@example.com'
    assert user.email_verified is True

    matched = await resolve_or_create_user(
        second,
        {'sub': 'b-1', 'email': 'alice@example.com', 'email_verified': True},
    )
    assert matched == user_id
    assert await _user_count(async_session_maker) == 1


@pytest.mark.asyncio
async def test_string_true_email_verified_matches_existing_user(
    idps, async_session_maker
):
    first, second = idps
    user_id = await resolve_or_create_user(
        first, {'sub': 'a-1', 'email': 'bob@example.com', 'email_verified': 'true'}
    )
    assert (await _get_user(async_session_maker, user_id)).email_verified is True

    matched = await resolve_or_create_user(
        second, {'sub': 'b-1', 'email': 'bob@example.com', 'email_verified': 'true'}
    )
    assert matched == user_id


@pytest.mark.asyncio
async def test_string_false_email_verified_does_not_take_over_account(
    idps, async_session_maker
):
    first, second = idps
    owner_id = await resolve_or_create_user(
        first, {'sub': 'a-1', 'email': 'carol@example.com', 'email_verified': True}
    )

    other_id = await resolve_or_create_user(
        second,
        {'sub': 'b-1', 'email': 'carol@example.com', 'email_verified': 'false'},
    )

    assert other_id != owner_id
    assert (await _get_user(async_session_maker, other_id)).email_verified is False


@pytest.mark.asyncio
async def test_email_verified_from_id_token_matches_existing_user(
    idps, async_session_maker
):
    first, second = idps
    owner_id = await resolve_or_create_user(
        first, {'sub': 'a-1', 'email': 'dave@example.com', 'email_verified': True}
    )
    userinfo = {'sub': 'g-1', 'email': 'dave@example.com'}
    token_data = {
        'id_token': _id_token(
            sub='g-1', aud='client', email='dave@example.com', email_verified=True
        )
    }

    merged = _merge_id_token_claims(second, userinfo, token_data)

    assert await resolve_or_create_user(second, merged) == owner_id
    assert await _user_count(async_session_maker) == 1


def _provider_with_client_id(client_id: str):
    provider = MagicMock()
    provider.id = 1
    provider.details = OAuthProviderDetails(
        provider_type=ProviderType.ENTERPRISE_SSO,
        client_id=client_id,
        authorization_url='https://idp.example.com/auth',
        token_url='https://idp.example.com/token',
        userinfo_url='https://idp.example.com/userinfo',
    )
    return provider


@pytest.mark.parametrize(
    'id_token_claims',
    [
        {'sub': 'g-1', 'aud': 'someone-else', 'email_verified': True},
        {'sub': 'g-2', 'aud': 'cid', 'email_verified': True},
    ],
    ids=['wrong-audience', 'wrong-subject'],
)
def test_id_token_claims_ignored_unless_aud_and_sub_match(id_token_claims):
    userinfo = {'sub': 'g-1', 'email': 'e@example.com'}

    merged = _merge_id_token_claims(
        _provider_with_client_id('cid'),
        userinfo,
        {'id_token': _id_token(**id_token_claims)},
    )

    assert merged == userinfo


def test_id_token_fills_only_missing_claims_and_accepts_audience_list():
    merged = _merge_id_token_claims(
        _provider_with_client_id('cid'),
        {'sub': 'g-1', 'email': 'e@example.com'},
        {
            'id_token': _id_token(
                sub='g-1',
                aud=['cid', 'other'],
                email='ignored@example.com',
                email_verified=True,
            )
        },
    )

    assert merged == {'sub': 'g-1', 'email': 'e@example.com', 'email_verified': True}


def test_id_token_not_consulted_when_userinfo_is_complete():
    userinfo = {'sub': 'g-1', 'email': 'e@example.com', 'email_verified': False}

    merged = _merge_id_token_claims(
        _provider_with_client_id('cid'),
        userinfo,
        {'id_token': _id_token(sub='g-1', aud='cid', email_verified=True)},
    )

    assert merged == userinfo


def test_unparseable_id_token_is_ignored():
    userinfo = {'sub': 'g-1', 'email': 'e@example.com'}

    merged = _merge_id_token_claims(
        _provider_with_client_id('cid'), userinfo, {'id_token': 'not-a-jwt'}
    )

    assert merged == userinfo
