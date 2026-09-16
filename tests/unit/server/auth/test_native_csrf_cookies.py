"""CSRF library configuration and session binding without account/route fixtures."""

from collections.abc import Iterator

import httpx
import pytest
from fastapi import HTTPException, Request, Response
from pydantic import SecretStr

from openhands.app_server.services.jwt_service import JwtService
from openhands.app_server.utils.encryption_key import EncryptionKey
from server.auth import auth_config, native_csrf
from server.auth.native_csrf import (
    CSRF_COOKIE,
    CSRF_MAX_AGE,
    LOCAL_CSRF_COOKIE,
    clear_csrf_cookie,
    issue_csrf,
    validate_csrf,
)
from server.auth.native_session import SESSION_COOKIE


@pytest.fixture(autouse=True)
def csrf_config(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv('OH_WEB_URL', 'https://native.example.com')
    monkeypatch.setenv('AUTH_ALLOW_INSECURE_LOCALHOST', 'false')
    auth_config.get_native_auth_settings.cache_clear()

    def jwt_service() -> JwtService:
        # Distinct service instances represent pods sharing the existing key.
        return JwtService([EncryptionKey(key=SecretStr('test-persistent-secret'))])

    monkeypatch.setattr(native_csrf, 'get_jwt_service', jwt_service)
    yield
    auth_config.get_native_auth_settings.cache_clear()


def browser_request(url: str, headers: dict[str, str] | None = None) -> Request:
    prepared = httpx.Request('POST', url, headers=headers)
    return Request(
        {
            'type': 'http',
            'method': 'POST',
            'scheme': prepared.url.scheme,
            'path': '/api/auth/csrf',
            'headers': [(name.lower(), value) for name, value in prepared.headers.raw],
            'query_string': b'',
        }
    )


async def test_tls_and_opted_in_localhost_cookie_policies_remain_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('OH_WEB_URL', 'http://localhost:3000')
    monkeypatch.setenv('AUTH_ALLOW_INSECURE_LOCALHOST', '1')
    auth_config.get_native_auth_settings.cache_clear()
    for url, cookie, secure in (
        ('https://native.example.com', CSRF_COOKIE, True),
        ('http://localhost:3000', LOCAL_CSRF_COOKIE, False),
        ('https://localhost:3000', CSRF_COOKIE, True),
        ('http://native.example.com', CSRF_COOKIE, True),
        ('http://localhost:3000', LOCAL_CSRF_COOKIE, False),
    ):
        issued = Response()
        token = issue_csrf(browser_request(url), issued, None)
        header = issued.headers['set-cookie']
        assert header.startswith(cookie + '=')
        assert ('Secure' in header) is secure
        for attribute in (
            'HttpOnly',
            'Path=/',
            'SameSite=lax',
            f'Max-Age={CSRF_MAX_AGE}',
        ):
            assert attribute in header
        assert 'Domain=' not in header
        submitted = browser_request(
            url, {'Cookie': header.split(';', 1)[0], 'X-CSRF-Token': token}
        )
        await validate_csrf(submitted)
        cleared = Response()
        clear_csrf_cookie(submitted, cleared)
        assert cleared.headers['set-cookie'].startswith(cookie + '=')
        assert ('Secure' in cleared.headers['set-cookie']) is secure
        assert 'Max-Age=0' in cleared.headers['set-cookie']


async def test_proof_requires_both_cookies_and_current_browser_identity() -> None:
    url = 'https://native.example.com'
    issued = Response()
    session = 'first-session-token'
    token = issue_csrf(browser_request(url), issued, session)
    csrf_cookie = issued.headers['set-cookie'].split(';', 1)[0]
    matching = browser_request(
        url,
        {
            'Cookie': f'{csrf_cookie}; {SESSION_COOKIE}={session}',
            'X-CSRF-Token': token,
        },
    )
    await validate_csrf(matching)
    for cookie in (
        csrf_cookie,
        f'{csrf_cookie}; {SESSION_COOKIE}=different-session-token',
        f'{SESSION_COOKIE}={session}',
    ):
        with pytest.raises(HTTPException) as rejected:
            await validate_csrf(
                browser_request(url, {'Cookie': cookie, 'X-CSRF-Token': token})
            )
        assert rejected.value.status_code == 403
        assert rejected.value.detail == 'Invalid CSRF token'
