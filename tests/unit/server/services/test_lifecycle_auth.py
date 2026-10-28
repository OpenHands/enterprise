from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest

from server.auth.auth_error import AuthError
from server.auth.saas_user_auth import (
    SaasUserAuth,
    saas_user_auth_from_oauth_v2_cookie,
    saas_user_auth_from_signed_token,
)
from storage.encrypt_utils import get_jwt_service
from storage.user import User


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
async def test_cookie_revocation_survives_enable(
    create_user, async_session_maker, monkeypatch, legacy
):
    monkeypatch.setattr('storage.user_store.a_session_maker', async_session_maker)
    user = create_user()
    if legacy:
        payload = {
            'access_token': jwt.encode(
                {'sub': str(user.id), 'email': None, 'email_verified': False},
                'test-key',
            ),
            'refresh_token': 'refresh',
        }
        authenticate = saas_user_auth_from_signed_token
    else:
        # The session claims every openhands_auth cookie carries; a bare
        # {user_id} JWS is no longer accepted as a login cookie.
        payload = {
            'user_id': str(user.id),
            'accepted_tos': True,
            'access_token_expires_at': None,
        }
        authenticate = saas_user_auth_from_oauth_v2_cookie
    old = get_jwt_service().create_jws_token(payload)
    assert (await authenticate(old)).user_id == str(user.id)
    async with async_session_maker() as session:
        stored = await session.get(User, user.id)
        stored.is_disabled = True
        stored.credentials_revoked_at = datetime.now(timezone.utc)
        await session.commit()
    with pytest.raises(AuthError):
        await authenticate(old)
    async with async_session_maker() as session:
        stored = await session.get(User, user.id)
        stored.is_disabled = False
        # Avoid sleeping; old cookie has the same integer second as revocation.
        stored.credentials_revoked_at = datetime.fromtimestamp(
            jwt.decode(old, options={'verify_signature': False})['iat'], timezone.utc
        )
        await session.commit()
    with pytest.raises(AuthError):
        await authenticate(old)
    from freezegun import freeze_time

    with freeze_time(datetime.now(timezone.utc) + timedelta(seconds=2)):
        fresh = get_jwt_service().create_jws_token(payload)
        assert (await authenticate(fresh)).user_id == str(user.id)


@pytest.mark.asyncio
async def test_background_rejects_disabled_and_missing(
    create_user, async_session_maker, monkeypatch
):
    monkeypatch.setattr('storage.user_store.a_session_maker', async_session_maker)
    user = create_user(is_disabled=True)
    for uid in (str(user.id), str(uuid4()), 'not-a-uuid'):
        with pytest.raises(AuthError):
            await SaasUserAuth.get_for_user(uid)


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['is_disabled', 'deletion_pending'])
async def test_integration_resolver_rejects_inactive(
    create_user, async_session_maker, monkeypatch, field
):
    from unittest.mock import AsyncMock

    from integrations.v1_utils import get_saas_user_auth

    monkeypatch.setattr('storage.user_store.a_session_maker', async_session_maker)
    user = create_user(**{field: True})
    tokens = AsyncMock()
    with pytest.raises(AuthError):
        await get_saas_user_auth(str(user.id), tokens)
    tokens.load_offline_token.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['is_disabled', 'deletion_pending'])
async def test_idp_completion_rejects_inactive(create_user, field):
    from starlette.requests import Request

    from server.routes.idp import _complete_idp_login

    user = create_user(**{field: True})
    response = await _complete_idp_login(
        request=Request(
            {
                'type': 'http',
                'headers': [],
                'path': '/',
                'scheme': 'https',
                'server': ('example.test', 443),
            }
        ),
        user=user,
        is_new_user=False,
        email='test@example.com',
        redirect_url='/',
    )
    assert response.status_code == 302
    assert response.headers['location'].endswith('/login?account_disabled=true')
    assert 'set-cookie' not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['is_disabled', 'deletion_pending'])
async def test_oauth_login_guard_checks_lifecycle(
    create_user, async_session_maker, monkeypatch, field
):
    from storage.user_store import UserStore

    monkeypatch.setattr('storage.user_store.a_session_maker', async_session_maker)
    user = create_user(**{field: True})
    assert await UserStore.is_user_disabled(str(user.id))
