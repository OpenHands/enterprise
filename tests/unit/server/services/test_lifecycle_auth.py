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
        payload = {'user_id': str(user.id)}
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
