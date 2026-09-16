"""Native browser transitions exercise real routes, cookies, and PostgreSQL."""

from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import delete, update

from server.auth.native_password import hash_password
from server.auth.native_types import SessionFactory
from server.routes import native_auth, orgs
from server.services.native_account_service import create_profile, mark_self_deleted
from server.services.native_auth_service import NativeAuthService, NativeLogin
from storage import org_store
from storage.api_key import ApiKey
from storage.api_key_store import ApiKeyStore
from storage.native_auth import AuthAccount, PasswordCredential
from storage.org import Org
from storage.org_member import OrgMember
from storage.user import User
from tests.unit.server.auth.native_test_types import (
    NativeRuntime,
    get_csrf_token,
    present,
)
from tests.unit.server.auth.test_native_runtime import ORIGIN, PASSWORD, native_app

pytest_plugins = ['tests.unit.server.auth.test_native_runtime']


@pytest.fixture
def browser_app(
    native_runtime: NativeRuntime,
    monkeypatch: pytest.MonkeyPatch,
    async_session_maker: SessionFactory,
) -> FastAPI:
    service, _, _ = native_runtime
    monkeypatch.setattr(native_auth, 'get_native_auth_service', lambda: service)
    monkeypatch.setattr(org_store, 'a_session_maker', async_session_maker)
    app = native_app()
    app.include_router(orgs.org_router)

    @app.get('/api/browser-principal')
    async def principal(request: Request) -> dict[str, str | None]:
        return {
            'id': await request.state.user_auth.get_user_id(),
            'transport': request.state.user_auth.credential_transport,
        }

    return app


def set_browser_cookie(client: httpx.AsyncClient, name: str, value: str) -> None:
    # A real response cookie is scoped to this host. An unscoped httpx cookie
    # would survive a correct host-scoped deletion and hide browser behavior.
    client.cookies.set(name, value, domain=present(urlsplit(ORIGIN).hostname), path='/')


async def accept_tos(async_session_maker: SessionFactory, account_id: UUID) -> None:
    async with async_session_maker() as session, session.begin():
        await session.execute(
            update(User)
            .where(User.id == account_id)
            .values(accepted_tos=datetime.now())
        )


async def assert_key_preserved(
    client: httpx.AsyncClient,
    api_key: str,
    account_id: UUID,
    async_session_maker: SessionFactory,
) -> None:
    key = await ApiKeyStore().validate_api_key(api_key)
    assert key is not None
    async with async_session_maker() as session:
        row = await session.get(ApiKey, key.key_id)
        assert row is not None
        assert row.user_id == str(account_id)
    for headers in (
        {'Authorization': f'Bearer {api_key}'},
        {'X-Access-Token': api_key},
    ):
        response = await client.get('/api/browser-principal', headers=headers)
        assert response.status_code == 200
        assert response.json() == {'id': str(account_id), 'transport': 'bearer'}


@pytest.mark.asyncio
@pytest.mark.parametrize('stale_key_valid', [True, False])
async def test_native_logout_expires_all_browser_identity_cookies(
    native_runtime: NativeRuntime,
    browser_app: FastAPI,
    async_session_maker: SessionFactory,
    stale_key_valid: bool,
) -> None:
    service, login, api_key = native_runtime
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=browser_app), base_url=ORIGIN
    ) as client:
        set_browser_cookie(client, 'openhands_session', login.token)
        set_browser_cookie(client, 'api_key', api_key if stale_key_valid else 'invalid')
        response = await client.post(
            '/api/logout',
            headers={'Origin': ORIGIN, 'X-CSRF-Token': await get_csrf_token(client)},
        )
        assert response.status_code == 200
        assert 'openhands_session' not in list(client.cookies)
        assert 'api_key' not in list(client.cookies)
        expired = [
            value
            for value in response.headers.get_list('set-cookie')
            if value.startswith('api_key=')
        ]
        assert len(expired) == 1
        for attribute in ('Max-Age=0', 'HttpOnly', 'Path=/', 'SameSite=lax', 'Secure'):
            assert attribute in expired[0]
        assert 'Domain=' not in expired[0]
        assert await service.authenticate_session(login.token) is None
        csrf = (await client.get('/api/auth/csrf')).json()['csrf_token']
        after = await client.post(
            '/api/authenticate', headers={'Origin': ORIGIN, 'X-CSRF-Token': csrf}
        )
        assert after.status_code == 401
        await assert_key_preserved(
            client, api_key, login.principal.account_id, async_session_maker
        )


@pytest.mark.asyncio
@pytest.mark.parametrize('stale_key_valid', [True, False])
async def test_native_sign_in_replaces_previous_api_key_cookie_identity(
    native_runtime: NativeRuntime,
    browser_app: FastAPI,
    async_session_maker: SessionFactory,
    stale_key_valid: bool,
) -> None:
    service, previous, api_key = native_runtime
    email = 'second@example.com'
    await create_local_account(service, async_session_maker, email)
    path = '/api/auth/password/login'
    body = {'email': email, 'password': PASSWORD}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=browser_app), base_url=ORIGIN
    ) as client:
        set_browser_cookie(client, 'openhands_session', previous.token)
        set_browser_cookie(client, 'api_key', api_key if stale_key_valid else 'invalid')
        response = await client.post(
            path,
            json=body,
            headers={'Origin': ORIGIN, 'X-CSRF-Token': await get_csrf_token(client)},
        )
        assert response.status_code == 200
        assert 'api_key' not in list(client.cookies)
        current = await service.authenticate_session(
            client.cookies['openhands_session']
        )
        assert current is not None
        assert current.email == email
        assert current.account_id != previous.principal.account_id
        assert await service.authenticate_session(previous.token) is not None
        await accept_tos(async_session_maker, current.account_id)
        actual = await client.get('/api/browser-principal')
        assert actual.status_code == 200
        assert actual.json() == {
            'id': str(current.account_id),
            'transport': 'native_cookie',
        }
        await assert_key_preserved(
            client, api_key, previous.principal.account_id, async_session_maker
        )


@pytest.mark.asyncio
async def test_native_session_identity_wins_over_residual_api_key_cookie(
    native_runtime: NativeRuntime,
    browser_app: FastAPI,
    async_session_maker: SessionFactory,
) -> None:
    service, previous, api_key = native_runtime
    current = await create_local_account(
        service, async_session_maker, 'second@example.com'
    )
    await accept_tos(async_session_maker, present(current).principal.account_id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=browser_app), base_url=ORIGIN
    ) as client:
        set_browser_cookie(client, 'openhands_session', present(current).token)
        # Simulate an API-key cookie outside the scope that login can expire.
        client.cookies.set('api_key', api_key, domain='.example.com', path='/api')
        response = await client.get('/api/browser-principal')
        assert response.status_code == 200
        assert response.json() == {
            'id': str(present(current).principal.account_id),
            'transport': 'native_cookie',
        }
        await assert_key_preserved(
            client, api_key, previous.principal.account_id, async_session_maker
        )
        invalid = await client.get(
            '/api/browser-principal', headers={'Authorization': 'Bearer invalid'}
        )
        assert invalid.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize('account_kind', ['bootstrap', 'created', 'restored'])
async def test_native_profiles_can_list_personal_organization(
    native_runtime: NativeRuntime,
    browser_app: FastAPI,
    async_session_maker: SessionFactory,
    account_kind: str,
) -> None:
    service, login, _ = native_runtime
    if account_kind != 'bootstrap':
        login = await create_local_account(
            service, async_session_maker, 'second@example.com'
        )
    account_id = login.principal.account_id
    if account_kind == 'restored':
        async with async_session_maker() as session, session.begin():
            await mark_self_deleted(session, account_id)
            await session.execute(
                delete(OrgMember).where(OrgMember.user_id == account_id)
            )
            await session.execute(delete(User).where(User.id == account_id))
            await session.execute(delete(Org).where(Org.id == account_id))
        login = await service.login(
            login.principal.email, PASSWORD, client_ip='127.0.0.1'
        )
        assert login.principal.account_id == account_id
    await accept_tos(async_session_maker, account_id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=browser_app), base_url=ORIGIN
    ) as client:
        set_browser_cookie(client, 'openhands_session', login.token)
        response = await client.get('/api/organizations')
        assert response.status_code == 200
        body = response.json()
        assert body['current_org_id'] == str(account_id)
        personal = next(org for org in body['items'] if org['id'] == str(account_id))
        assert personal['contact_name'] == login.principal.email
        assert personal['contact_email'] == login.principal.email
        assert personal['is_personal'] is True


async def create_local_account(
    service: NativeAuthService, sessions: SessionFactory, email: str
) -> NativeLogin:
    async with sessions() as session, session.begin():
        account = AuthAccount(id=uuid4(), normalized_email=email, display_email=email)
        session.add(account)
        await session.flush()
        credential = PasswordCredential(
            account_id=account.id,
            normalized_login_email=email,
            display_email=email,
            password_hash=await hash_password(PASSWORD),
        )
        session.add(credential)
        await create_profile(session, account, email)
    return await service.login(email, PASSWORD, client_ip='127.0.0.1')
