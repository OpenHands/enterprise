"""Exercise native authentication against the migrated PostgreSQL schema."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.auth import auth_config
from server.auth.native_csrf import CSRF_COOKIE
from server.auth.native_password import (
    NativeAuthError,
)
from server.auth.native_session import (
    SESSION_COOKIE,
)
from server.services.native_auth_service import (
    safe_return_path,
)
from tests.unit.server.auth.native_test_types import (
    NativeFixture,
    get_csrf_token,
)

PASSWORD = 'Long native test passphrase 739!'
NEW_PASSWORD = 'A replacement test passphrase 846!'


async def test_login_throttle_and_generic_errors(native: NativeFixture) -> None:
    service, _ = native
    for index in range(10):
        with pytest.raises(NativeAuthError, match='Invalid email or password') as error:
            await service.login('missing@example.com', 'wrong', client_ip=str(index))
        assert error.value.status_code == 401
    with pytest.raises(NativeAuthError) as error:
        await service.login('missing@example.com', PASSWORD, client_ip='different-ip')
    assert error.value.status_code == 429


async def test_expired_session_is_cleared_and_login_rotates_csrf_cookie(
    native: NativeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.routes import native_auth

    service, _ = native
    monkeypatch.setattr(native_auth, 'get_native_auth_service', lambda: service)
    app = FastAPI()
    app.include_router(native_auth.native_auth_router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='https://native.example.test'
    ) as client:
        client.cookies.set(
            SESSION_COOKIE,
            'expired-session-token' * 3,
            domain='native.example.test',
            path='/',
        )
        response = await client.get('/api/auth/csrf')
        assert response.status_code == 200
        assert not client.cookies.get(SESSION_COOKIE)
        assert response.headers['cache-control'] == 'no-store'
        proof = response.json()['csrf_token']
        assert client.cookies.get(CSRF_COOKIE)
        login = await client.post(
            '/api/auth/password/login',
            json={'email': 'admin@example.com', 'password': PASSWORD},
        )
        assert login.status_code == 200
        cookie = login.headers['set-cookie']
        assert (
            'Secure' in cookie
            and 'HttpOnly' in cookie
            and 'SameSite=lax' in cookie
            and 'Domain=' not in cookie
        )
        session_cookie = next(
            value
            for value in login.headers.get_list('set-cookie')
            if value.startswith(SESSION_COOKIE + '=')
        )
        assert 'Max-Age=' not in session_cookie
        assert 'Expires=' not in session_cookie
        assert CSRF_COOKIE not in client.cookies
        assert await get_csrf_token(client) != proof
        assert client.cookies.get(CSRF_COOKIE)


@pytest.mark.parametrize(
    'value',
    ['//evil.test', '/\\evil.test', 'https://evil.test', '/%2Fevil.test', '/%0aevil'],
)
def test_redirect_rejects_external_and_control_paths(value: str) -> None:
    assert safe_return_path(value) == '/'


async def test_native_routes_reject_keycloak_mode_and_redact_password_validation(
    native: NativeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.routes import native_auth

    service, _ = native
    monkeypatch.setattr(native_auth, 'get_native_auth_service', lambda: service)
    app = FastAPI()
    app.include_router(native_auth.native_auth_router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='https://native.example.test'
    ) as client:
        invalid = await client.post(
            '/api/auth/password/login', json={'email': 3, 'password': PASSWORD}
        )
        assert invalid.status_code == 422
        assert PASSWORD not in invalid.text
        assert 'input' not in invalid.text
        monkeypatch.setattr(auth_config, 'ENABLE_KEYCLOAK', True)
        disabled = await client.get('/api/auth/csrf')
        assert disabled.status_code == 404


pytest_plugins = ['tests.unit.server.auth.test_native_account_provisioning']
