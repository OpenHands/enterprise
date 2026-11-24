"""Email change and verification must not depend on refreshing the session.

Runs the real ``/api/email`` routes with a real ``SaasUserAuth`` and the real
``UserStore`` on the test database. Only Keycloak's admin API is an in-memory
fake.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI, Request
from pydantic import SecretStr
from sqlalchemy import select

from openhands.app_server.services.jwt_service import JwtAudience
from openhands.app_server.user_auth.user_auth import AuthType
from server.auth.saas_user_auth import SaasUserAuth
from server.routes.email import api_router
from storage import encrypt_utils
from storage.user import User


class FakeKeycloakAdmin:
    def __init__(self, email: str):
        self.calls: list[str] = []
        self.email = email
        self.email_verified = True

    def get_user(self, user_id):
        self.calls.append('get_user')
        return {'enabled': True, 'username': 'u', 'email': self.email}

    async def a_get_user(self, user_id):
        self.calls.append('a_get_user')
        return {'email': self.email, 'emailVerified': self.email_verified}

    async def a_update_user(self, user_id, payload):
        self.calls.append('a_update_user')
        self.email = payload['email']
        self.email_verified = payload['emailVerified']

    async def a_send_verify_email(self, user_id, redirect_uri, client_id):
        self.calls.append('a_send_verify_email')


@pytest.fixture
def keycloak() -> FakeKeycloakAdmin:
    return FakeKeycloakAdmin('old@example.com')


@pytest.fixture
def make_client(oauth_v2_stores, async_session_maker, keycloak):
    clients: list[httpx.AsyncClient] = []

    def _make(user_auth_for: Callable[[Request], object]) -> httpx.AsyncClient:
        app = FastAPI()

        @app.middleware('http')
        async def attach_user_auth(request: Request, call_next):
            # What SaasUserAuthMiddleware / get_user_auth do for a cookie request.
            request.state.user_auth = await user_auth_for(request)
            return await call_next(request)

        app.include_router(api_router)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://test'
        )
        clients.append(client)
        return client

    with (
        patch('storage.user_authorization_store.a_session_maker', async_session_maker),
        patch('server.routes.email.get_keycloak_admin', return_value=keycloak),
    ):
        yield _make


def _user_row(session_maker, user_id) -> User:
    with session_maker() as session:
        return session.execute(select(User).where(User.id == user_id)).scalar_one()


def _v2_cookie(user_id) -> str:
    return encrypt_utils.get_jwt_service().create_jws_token(
        {
            'user_id': str(user_id),
            'access_token_expires_at': None,
            'accepted_tos': True,
            'idp_provider_id': None,
            'iat': int(time.time()),
        },
        expires_in=timedelta(days=30),
        audience=JwtAudience.SESSION,
    )


@pytest.mark.asyncio
async def test_oauth_v2_session_changes_email_and_sends_verification(
    make_client, keycloak, create_user, session_maker
):
    user = create_user(email='old@example.com', email_verified=True)

    async def from_cookie(request: Request):
        return await SaasUserAuth.get_instance(request)

    async with make_client(from_cookie) as client:
        client.cookies.set('openhands_auth', _v2_cookie(user.id))
        response = await client.post('/api/email', json={'email': 'new@example.com'})

    assert response.status_code == 200
    assert response.json() == {'message': 'Email changed'}
    assert 'keycloak_auth' not in response.headers.get('set-cookie', '')
    assert keycloak.calls == ['get_user', 'a_update_user', 'a_send_verify_email']
    row = _user_row(session_maker, user.id)
    assert (row.email, row.email_verified) == ('new@example.com', False)


@pytest.mark.asyncio
@pytest.mark.parametrize('verified_in_keycloak', [True, False])
async def test_oauth_v2_session_verified_callback_follows_keycloak(
    make_client, keycloak, create_user, session_maker, verified_in_keycloak
):
    user = create_user(email='new@example.com', email_verified=False)
    keycloak.email_verified = verified_in_keycloak

    async def from_cookie(request: Request):
        return await SaasUserAuth.get_instance(request)

    async with make_client(from_cookie) as client:
        client.cookies.set('openhands_auth', _v2_cookie(user.id))
        response = await client.get('/api/email/verified')

    assert response.status_code == 302
    assert response.headers['location'].endswith('://test/settings/user')
    assert _user_row(session_maker, user.id).email_verified is verified_in_keycloak


@pytest.mark.asyncio
async def test_failed_session_refresh_still_sends_verification(
    make_client, keycloak, create_user, session_maker
):
    """A legacy session whose refresh token cannot be decoded."""
    user = create_user(email='old@example.com', email_verified=True)

    async def legacy_session(request: Request):
        return SaasUserAuth(
            user_id=str(user.id),
            refresh_token=SecretStr('not-a-jwt'),
            auth_type=AuthType.COOKIE,
            accepted_tos=True,
        )

    async with make_client(legacy_session) as client:
        response = await client.post('/api/email', json={'email': 'new@example.com'})

    assert response.status_code == 200
    assert response.json() == {'message': 'Email changed', 'reauth_required': True}
    assert keycloak.calls == ['get_user', 'a_update_user', 'a_send_verify_email']
    row = _user_row(session_maker, user.id)
    assert (row.email, row.email_verified) == ('new@example.com', False)
