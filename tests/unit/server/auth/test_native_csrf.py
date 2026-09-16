"""Exercise the CSRF library through the browser middleware and real auth routes."""

import asyncio
import time
from unittest.mock import patch

import httpx
import pytest
from itsdangerous import TimestampSigner

from server.auth.native_csrf import CSRF_COOKIE, CSRF_MAX_AGE
from server.auth.native_session import SESSION_COOKIE
from server.auth.native_types import SessionFactory
from server.routes import native_enrollment, native_password
from server.services.native_enrollment_service import NativeEnrollmentService
from server.services.native_password_service import NativePasswordService
from tests.unit.server.auth.native_test_types import NativeRuntime, get_csrf_token
from tests.unit.server.auth.test_native_runtime import ORIGIN, PASSWORD, native_app

pytest_plugins = ['tests.unit.server.auth.test_native_runtime']


def set_cookie(client: httpx.AsyncClient, name: str, value: str) -> None:
    client.cookies.set(name, value, domain=client.base_url.host, path='/')


@pytest.mark.parametrize('credential', ['session', 'api_key'])
@pytest.mark.parametrize(
    'failure',
    [
        'missing_cookie',
        'missing_header',
        'mismatch',
        'tampered',
        'expired',
        'body_only',
    ],
)
async def test_library_rejects_invalid_proof_before_cookie_mutation(
    native_runtime: NativeRuntime, credential: str, failure: str
) -> None:
    _, login, api_key = native_runtime
    app = native_app()
    mutations = 0

    @app.post('/api/v1/csrf-write')
    async def write() -> dict[str, bool]:
        nonlocal mutations
        mutations += 1
        return {'accepted': True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        if credential == 'session':
            set_cookie(client, SESSION_COOKIE, login.token)
        else:
            set_cookie(client, 'api_key', api_key)
        if failure == 'expired':
            # Advance only the library signer's clock; randomness, signing and
            # signature verification are all exercised by the installed library.
            with patch.object(
                TimestampSigner,
                'get_timestamp',
                return_value=int(time.time()) - CSRF_MAX_AGE - 1,
            ):
                token = await get_csrf_token(client)
        else:
            token = await get_csrf_token(client)
        headers = {'Origin': ORIGIN, 'X-CSRF-Token': token}
        if failure == 'missing_cookie':
            client.cookies.delete(CSRF_COOKIE)
        elif failure in ('missing_header', 'body_only'):
            del headers['X-CSRF-Token']
        elif failure == 'mismatch':
            headers['X-CSRF-Token'] = 'not-the-issued-token'
        elif failure == 'tampered':
            signed = client.cookies[CSRF_COOKIE]
            set_cookie(client, CSRF_COOKIE, 'x' + signed[1:])
        response = await client.post(
            '/api/v1/csrf-write', headers=headers, json={'csrf_token': token}
        )
    assert response.status_code == 403
    assert response.json() == {'detail': 'Invalid CSRF token'}
    assert response.headers['access-control-allow-origin'] == ORIGIN
    assert response.headers['access-control-allow-credentials'] == 'true'
    assert mutations == 0


@pytest.mark.parametrize(
    ('path', 'body', 'accepted_status'),
    [
        (
            '/api/auth/password/login',
            {'email': 'native@example.com', 'password': PASSWORD},
            200,
        ),
        ('/api/auth/enrollment/inspect', {'token': 'invalid'}, 400),
        (
            '/api/auth/enrollment/complete',
            {'token': 'invalid', 'password': PASSWORD},
            400,
        ),
        (
            '/api/auth/password/reset/complete',
            {'token': 'invalid', 'new_password': PASSWORD},
            400,
        ),
    ],
)
async def test_anonymous_auth_routes_require_library_proof(
    native_runtime: NativeRuntime,
    async_session_maker: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    body: dict[str, str],
    accepted_status: int,
) -> None:
    enrollment = NativeEnrollmentService(async_session_maker)
    passwords = NativePasswordService(async_session_maker)
    monkeypatch.setattr(
        native_enrollment, 'get_native_enrollment_service', lambda: enrollment
    )
    monkeypatch.setattr(
        native_password, 'get_native_password_service', lambda: passwords
    )
    app = native_app()
    app.include_router(native_enrollment.native_enrollment_router)
    app.include_router(native_password.native_password_router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        missing = await client.post(path, json=body, headers={'Origin': ORIGIN})
        assert missing.status_code == 403
        token = await get_csrf_token(client)
        response = await client.post(
            path, json=body, headers={'Origin': ORIGIN, 'X-CSRF-Token': token}
        )
        assert response.status_code == accepted_status


async def test_login_and_logout_invalidate_previous_identity_proof(
    native_runtime: NativeRuntime,
) -> None:
    service, _, _ = native_runtime
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=native_app()), base_url=ORIGIN
    ) as client:
        anonymous = await get_csrf_token(client)
        anonymous_cookie = client.cookies[CSRF_COOKIE]
        login = await client.post(
            '/api/auth/password/login',
            json={'email': 'native@example.com', 'password': PASSWORD},
            headers={'Origin': ORIGIN, 'X-CSRF-Token': anonymous},
        )
        assert login.status_code == 200
        session = client.cookies[SESSION_COOKIE]
        assert CSRF_COOKIE not in client.cookies
        # Replaying the complete old signed pair still cannot cross identities.
        set_cookie(client, CSRF_COOKIE, anonymous_cookie)
        old_anonymous = await client.post(
            '/api/v1/native-test',
            headers={'Origin': ORIGIN, 'X-CSRF-Token': anonymous},
        )
        assert old_anonymous.status_code == 403
        proof = await get_csrf_token(client)
        signed = client.cookies[CSRF_COOKIE]
        valid = await client.post(
            '/api/v1/native-test', headers={'Origin': ORIGIN, 'X-CSRF-Token': proof}
        )
        assert valid.status_code == 200
        logout = await client.post(
            '/api/logout', headers={'Origin': ORIGIN, 'X-CSRF-Token': proof}
        )
        assert logout.status_code == 200
        assert SESSION_COOKIE not in client.cookies
        assert CSRF_COOKIE not in client.cookies
        assert await service.authenticate_session(session) is None
        expired_cookie = next(
            value
            for value in logout.headers.get_list('set-cookie')
            if value.startswith(CSRF_COOKIE + '=')
        )
        for attribute in ('Max-Age=0', 'HttpOnly', 'Path=/', 'SameSite=lax', 'Secure'):
            assert attribute in expired_cookie
        set_cookie(client, CSRF_COOKIE, signed)
        old_session = await client.post(
            '/api/auth/password/login',
            json={'email': 'native@example.com', 'password': PASSWORD},
            headers={'Origin': ORIGIN, 'X-CSRF-Token': proof},
        )
        assert old_session.status_code == 403
        current = await get_csrf_token(client)
        again = await client.post(
            '/api/auth/password/login',
            json={'email': 'native@example.com', 'password': PASSWORD},
            headers={'Origin': ORIGIN, 'X-CSRF-Token': current},
        )
        assert again.status_code == 200
        set_cookie(client, CSRF_COOKIE, signed)
        old_identity = await client.post(
            '/api/v1/native-test', headers={'Origin': ORIGIN, 'X-CSRF-Token': proof}
        )
        assert old_identity.status_code == 403


@pytest.mark.parametrize(
    'path',
    [
        '/api/auth/password/login',
        '/api/logout',
        '/api/auth/password/change',
        '/integration/jira/workspaces/link',
        '/api/admin/auth-accounts/account-id/password-reset',
    ],
)
async def test_bearer_does_not_make_revoked_browser_proof_eligible(
    native_runtime: NativeRuntime, path: str
) -> None:
    service, login, api_key = native_runtime
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=native_app()), base_url=ORIGIN
    ) as client:
        set_cookie(client, SESSION_COOKIE, login.token)
        proof = await get_csrf_token(client)
        await service.revoke_session(login.token)
        response = await client.post(
            path,
            headers={
                'Origin': ORIGIN,
                'X-CSRF-Token': proof,
                'Authorization': f'Bearer {api_key}',
            },
        )
        assert response.status_code == 403
        assert response.json() == {'detail': 'Invalid CSRF token'}
        if path == '/api/auth/password/login':
            # The refresh endpoint repairs stale browser cookies for login.
            await get_csrf_token(client)
            assert SESSION_COOKIE not in client.cookies


async def test_other_tab_cookie_rotation_refreshes_before_exactly_one_mutation(
    native_runtime: NativeRuntime,
) -> None:
    _, login, _ = native_runtime
    app = native_app()
    mutations = 0

    @app.post('/api/v1/csrf-write')
    async def write() -> dict[str, int]:
        nonlocal mutations
        mutations += 1
        return {'mutations': mutations}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as browser:
        set_cookie(browser, SESSION_COOKIE, login.token)
        # Tabs share a cookie jar, but retain their own in-memory header token.
        first, second = await asyncio.gather(
            get_csrf_token(browser), get_csrf_token(browser)
        )
        assert first != second
        responses = [
            await browser.post(
                '/api/v1/csrf-write',
                headers={'Origin': ORIGIN, 'X-CSRF-Token': token},
            )
            for token in (first, second)
        ]
        assert sorted(response.status_code for response in responses) == [200, 403]
        assert mutations == 1
        rejected = next(
            response for response in responses if response.status_code == 403
        )
        assert rejected.json() == {'detail': 'Invalid CSRF token'}
        # Only the rejected operation is retried, with fresh shared cookie/proof.
        refreshed = await get_csrf_token(browser)
        accepted = await browser.post(
            '/api/v1/csrf-write',
            headers={'Origin': ORIGIN, 'X-CSRF-Token': refreshed},
        )
        assert accepted.status_code == 200
        assert mutations == 2
