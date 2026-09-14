"""Exercise native authentication against the migrated PostgreSQL schema."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.auth import auth_config
from server.auth.native_password import (
    NativeAuthError,
)
from server.auth.native_session import (
    ANONYMOUS_CSRF_COOKIE,
    SESSION_COOKIE,
)
from server.auth.native_types import SessionFactory
from server.services.native_auth_service import (
    safe_return_path,
)
from storage.native_auth import (
    BrowserSession,
)
from tests.unit.server.auth.native_test_types import (
    NativeFixture,
    present,
)

PASSWORD = 'Long native test passphrase 739!'
NEW_PASSWORD = 'A replacement test passphrase 846!'


async def test_login_throttle_and_generic_errors(native: NativeFixture) -> None:
    service, _ = native
    for index in range(10):
        with pytest.raises(NativeAuthError, match='Invalid email or password') as error:
            await service.login('missing@example.test', 'wrong', client_ip=str(index))
        assert error.value.status_code == 401
    with pytest.raises(NativeAuthError) as error:
        await service.login('missing@example.test', PASSWORD, client_ip='different-ip')
    assert error.value.status_code == 429


async def test_csrf_bound_to_cookie_and_expired_cookie_is_cleared(
    native: NativeFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from server.routes import native_auth

    service, _ = native
    monkeypatch.setattr(native_auth, 'get_native_auth_service', lambda: service)
    csrf_token, anonymous = await service.issue_csrf(None)
    assert await service.validate_csrf(None, anonymous, csrf_token)
    assert not await service.validate_csrf(None, 'other' * 10, csrf_token)
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
        assert await service.validate_csrf(
            client.cookies.get(SESSION_COOKIE),
            client.cookies.get(ANONYMOUS_CSRF_COOKIE),
            proof,
        )
        login = await client.post(
            '/api/auth/password/login',
            json={'email': 'admin@example.test', 'password': PASSWORD},
        )
        assert login.status_code == 200
        cookie = login.headers['set-cookie']
        assert (
            'Secure' in cookie
            and 'HttpOnly' in cookie
            and 'SameSite=lax' in cookie
            and 'Domain=' not in cookie
        )
        new_proof, anon = await service.issue_csrf(client.cookies.get(SESSION_COOKIE))
        assert anon is None
        assert await service.validate_csrf(
            client.cookies.get(SESSION_COOKIE), None, new_proof
        )
        assert not await service.validate_csrf(
            client.cookies.get(SESSION_COOKIE), None, proof
        )


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


async def test_recent_auth_and_absolute_expiry_cannot_be_extended(
    native: NativeFixture, configured: SessionFactory
) -> None:
    service, admin_id = native
    login = await service.login('admin@example.test', PASSWORD, client_ip='1')
    async with configured() as session, session.begin():
        row = await session.get(BrowserSession, login.principal.session_id)
        present(row).auth_time = datetime.now(UTC) - timedelta(minutes=16)
    with pytest.raises(NativeAuthError, match='Sign in again'):
        async with configured() as session, session.begin():
            await service._recent(session, login.token, admin_id)
    async with configured() as session, session.begin():
        row = await session.get(BrowserSession, login.principal.session_id)
        present(row).absolute_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        present(row).idle_expires_at = datetime.now(UTC) + timedelta(minutes=30)
    assert await service.authenticate_session(login.token) is None


pytest_plugins = ['tests.unit.server.auth.test_native_account_provisioning']
