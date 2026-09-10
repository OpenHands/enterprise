import smtplib
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.requests import Request

from server.auth import browser_security, mode
from server.auth.contracts import IssuedSession, Principal
from server.auth.local import throttle as limits
from server.auth.local.actions import ActionEmail, InvalidActionToken
from server.auth.local.passwords import PasswordPolicyError
from server.routes import local_auth


@pytest.fixture
def browser(monkeypatch):
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.LOCAL)
    monkeypatch.setenv('WEB_HOST', 'app.example.com')
    monkeypatch.setattr(local_auth, 'throttle', AsyncMock())
    app = FastAPI()
    app.include_router(local_auth.router)
    with TestClient(app, base_url='https://app.example.com') as client:
        yield client, app


def post(client, path, body):
    token = client.get('/api/auth/csrf').json()['csrf_token']
    return client.post(
        '/api/auth' + path,
        json=body,
        headers={'Origin': 'https://app.example.com', 'X-CSRF-Token': token},
    )


def test_csrf_preserves_seed_and_cookie_attributes(browser):
    client, _ = browser
    first = client.get('/api/auth/csrf')
    second = client.get('/api/auth/csrf')
    assert first.json() == second.json()
    cookie = first.headers['set-cookie']
    assert 'Secure' in cookie and 'SameSite=lax' in cookie and 'Path=/' in cookie
    assert 'Domain=' not in cookie and 'HttpOnly' not in cookie


def test_csrf_bound_to_session_and_valid_signature(browser):
    client, _ = browser
    token = client.get('/api/auth/csrf').json()['csrf_token']
    client.cookies.set('oh_session', 'changed-session')
    assert (
        client.post(
            '/api/auth/login',
            json={'email': 'a@example.com', 'password': 'secret'},
            headers={'Origin': 'https://app.example.com', 'X-CSRF-Token': token},
        ).status_code
        == 403
    )
    client.cookies.set('oh_csrf', 'attacker-token')
    assert (
        client.post(
            '/api/auth/login',
            json={'email': 'a@example.com', 'password': 'secret'},
            headers={
                'Origin': 'https://app.example.com',
                'X-CSRF-Token': 'attacker-token',
            },
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    'origin',
    [
        None,
        'null',
        'https://evil.example.com',
        'http://app.example.com',
        'https://app.example.com.evil.com',
    ],
)
def test_login_rejects_missing_and_cross_origin(browser, origin):
    client, _ = browser
    token = client.get('/api/auth/csrf').json()['csrf_token']
    headers = {'X-CSRF-Token': token}
    if origin:
        headers['Origin'] = origin
    assert (
        client.post(
            '/api/auth/login',
            json={'email': 'a@example.com', 'password': 'secret'},
            headers=headers,
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    'redirect',
    [
        'https://evil.example.com/',
        '//evil.example.com/',
        '/%2Fevil.example.com',
        '/%252Fevil.example.com',
        '/\\evil.example.com',
        '/%5cevil.example.com',
        '/%0d%0aevil',
        'javascript:alert(1)',
    ],
)
def test_unsafe_redirects_rejected(redirect):
    with pytest.raises(HTTPException):
        browser_security.safe_redirect(redirect)


@pytest.mark.parametrize(
    'redirect',
    [
        '/auth/reset-password#token=secret',
        '/path?access_token=secret',
        '/path?returnTo=%2Fauth%2Fverify-email%23token%3Dsecret',
    ],
)
def test_return_destinations_cannot_carry_credentials(redirect):
    with pytest.raises(HTTPException):
        browser_security.safe_redirect(redirect)


@pytest.mark.parametrize(
    'redirect',
    ['/', '/settings', '/device?user_code=abc123', '/organizations?tab=members#invite'],
)
def test_safe_redirects_preserved(redirect):
    assert browser_security.safe_redirect(redirect) == redirect


@pytest.mark.parametrize('cookie', ['api_key', 'keycloak_auth_1'])
def test_csrf_bound_to_api_key_and_reconstructed_keycloak_session(browser, cookie):
    client, _ = browser
    client.cookies.set('keycloak_auth', 'first-chunk')
    client.cookies.set(cookie, 'original-value')
    token = client.get('/api/auth/csrf').json()['csrf_token']
    client.cookies.set(cookie, 'replacement-value')
    response = client.post(
        '/api/auth/login',
        json={'email': 'user@example.com', 'password': 'secret'},
        headers={'Origin': 'https://app.example.com', 'X-CSRF-Token': token},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ('error', 'code'),
    [
        (InvalidActionToken('untrusted reset-token exception'), 'invalid_token'),
        (PasswordPolicyError('untrusted password exception'), 'invalid_password'),
    ],
)
def test_action_errors_use_stable_codes_without_exception_text(browser, error, code):
    client, app = browser
    actions = SimpleNamespace(reset_password=AsyncMock(side_effect=error))
    app.dependency_overrides[local_auth.account_actions] = lambda: actions
    response = post(
        client,
        '/password/reset',
        {'token': 'reset-token', 'new_password': 'new password value'},
    )
    assert response.status_code == 400
    assert response.json() == {'detail': {'code': code}}
    assert 'untrusted' not in response.text


def test_password_validation_never_echoes_raw_input(browser):
    client, _ = browser
    secret = 'this must never appear in response'
    result = post(
        client, '/login', {'email': 'x@example.com', 'password': {'secret': secret}}
    )
    assert result.status_code == 422
    assert secret not in result.text
    assert 'input' not in result.json()['detail'].lower()


def test_http_browser_origin_is_supported_without_emailing_secrets(
    browser, monkeypatch
):
    client, _ = browser
    token = client.get('/api/auth/csrf').json()['csrf_token']
    monkeypatch.setenv('WEB_HOST', 'http://localhost:3000')
    monkeypatch.setattr(local_auth.SMTPEmailService, 'is_configured', lambda: True)
    request = Request(
        {
            'type': 'http',
            'scheme': 'http',
            'server': ('localhost', 3000),
            'path': '/',
            'headers': [
                (b'origin', b'http://localhost:3000'),
                (b'cookie', f'oh_csrf={token}'.encode()),
                (b'x-csrf-token', token.encode()),
            ],
        }
    )
    browser_security.validate_csrf(request)
    assert not browser_security.auth_email_configured()
    with pytest.raises(ValueError, match='HTTPS'):
        browser_security.web_origin()


@pytest.mark.parametrize(
    'origin',
    [
        'https://app.example.com/path',
        'https://user:password@app.example.com',
        'https://[invalid',
        'https://app.example.com:invalid',
        'https://bad host',
    ],
)
def test_malformed_browser_origin_configuration_returns_403(
    browser, monkeypatch, origin
):
    client, _ = browser
    monkeypatch.setenv('WEB_HOST', origin)
    response = post(
        client, '/login', {'email': 'user@example.com', 'password': 'secret'}
    )
    assert response.status_code == 403


def test_keycloak_mode_rejects_local_mutations_but_serves_csrf(browser, monkeypatch):
    client, _ = browser
    monkeypatch.setattr(mode, '_auth_mode', mode.AuthMode.KEYCLOAK)
    assert client.get('/api/auth/csrf').status_code == 200
    assert (
        post(
            client, '/login', {'email': 'x@example.com', 'password': 'secret'}
        ).status_code
        == 404
    )


def test_initial_login_is_restricted_and_cookie_is_secure(browser):
    client, app = browser
    now = datetime.now(UTC)
    issued = IssuedSession(
        Principal(uuid4(), 'password', now, restricted=True),
        SecretStr('session-secret'),
        now + timedelta(hours=24),
    )
    passwords = SimpleNamespace(login=AsyncMock(return_value=issued))
    app.dependency_overrides[local_auth.password_service] = lambda: passwords
    result = post(
        client,
        '/login',
        {
            'email': 'x@example.com',
            'password': 'secret',
            'redirect_url': '/device',
            'invitation_token': 'invitation',
        },
    )
    assert result.status_code == 200
    assert result.json()['password_change_required']
    assert (
        result.json()['redirect_url']
        == '/auth/change-password?returnTo=%2Fdevice&invitation_token=invitation'
    )
    session_cookie = next(
        value
        for value in result.headers.get_list('set-cookie')
        if value.startswith('oh_session=')
    )
    assert all(
        value in session_cookie
        for value in ['HttpOnly', 'Secure', 'SameSite=lax', 'Max-Age=86400', 'Path=/']
    )
    assert 'Domain=' not in session_cookie
    assert 'session-secret' not in result.text


def test_admission_failure_revokes_issued_session(browser, monkeypatch):
    client, app = browser
    now = datetime.now(UTC)
    issued = IssuedSession(
        Principal(uuid4(), 'password', now),
        SecretStr('session-secret'),
        now + timedelta(hours=24),
    )
    backend = SimpleNamespace(revoke=AsyncMock())
    app.dependency_overrides[local_auth.password_service] = lambda: SimpleNamespace(
        login=AsyncMock(return_value=issued)
    )
    app.dependency_overrides[local_auth.session_backend] = lambda: backend
    admission = SimpleNamespace(
        complete_local_login=AsyncMock(
            side_effect=HTTPException(403, 'Admission denied')
        )
    )
    monkeypatch.setitem(sys.modules, 'server.auth.admission', admission)
    result = post(client, '/login', {'email': 'x@example.com', 'password': 'secret'})
    assert result.status_code == 403
    backend.revoke.assert_awaited_once_with(issued.token)
    assert 'oh_session=' not in result.headers.get('set-cookie', '')


def test_password_reset_does_not_login_and_preserves_invitation(browser):
    client, app = browser
    actions = SimpleNamespace(reset_password=AsyncMock())
    app.dependency_overrides[local_auth.account_actions] = lambda: actions
    result = post(
        client,
        '/password/reset',
        {
            'token': 'reset-secret',
            'new_password': 'a new password value',
            'redirect_url': '/device',
            'invitation_token': 'invite',
        },
    )
    assert result.status_code == 200
    assert (
        result.json()['redirect_url']
        == '/login?returnTo=%2Fdevice&invitation_token=invite'
    )
    assert 'Max-Age=0' in result.headers['set-cookie']


@pytest.mark.parametrize('configured', [False, True])
def test_recovery_is_generic_with_and_without_smtp(browser, monkeypatch, configured):
    client, app = browser
    monkeypatch.setattr(local_auth, 'auth_email_configured', lambda: configured)
    actions = SimpleNamespace(request_reset=AsyncMock(return_value=None))
    app.dependency_overrides[local_auth.account_actions] = lambda: actions
    first = post(client, '/password/forgot', {'email': 'unknown@example.com'})
    actions.request_reset.return_value = ActionEmail(
        'known@example.com', 'password_reset', SecretStr('reset-token')
    )
    monkeypatch.setattr(
        local_auth.SMTPEmailService, 'send_auth_email', MagicMock(return_value=None)
    )
    second = post(client, '/password/forgot', {'email': 'known@example.com'})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == local_auth.GENERIC_MESSAGE


def test_missing_public_origin_disables_auth_email_and_never_uses_default(
    browser, monkeypatch
):
    client, app = browser
    monkeypatch.delenv('WEB_HOST')
    monkeypatch.setattr(local_auth.SMTPEmailService, 'is_configured', lambda: True)
    actions = SimpleNamespace(request_reset=AsyncMock())
    app.dependency_overrides[local_auth.account_actions] = lambda: actions
    assert not browser_security.auth_email_configured()
    with pytest.raises(ValueError, match='WEB_HOST'):
        browser_security.web_origin()
    result = post(client, '/password/forgot', {'email': 'known@example.com'})
    assert result.status_code == 200
    actions.request_reset.assert_not_awaited()


def test_auth_email_origin_comes_from_configuration(browser, monkeypatch):
    client, app = browser
    monkeypatch.setattr(local_auth, 'auth_email_configured', lambda: True)
    actions = SimpleNamespace(
        request_reset=AsyncMock(
            return_value=ActionEmail(
                'known@example.com', 'password_reset', SecretStr('reset-token')
            )
        )
    )
    app.dependency_overrides[local_auth.account_actions] = lambda: actions
    send = MagicMock()
    monkeypatch.setattr(local_auth.SMTPEmailService, 'send_auth_email', send)
    result = post(
        client,
        '/password/forgot',
        {'email': 'known@example.com', 'redirect_url': '/device'},
    )
    assert result.status_code == 200
    link = send.call_args.args[2]
    assert link.startswith('https://app.example.com/auth/reset-password?')
    assert 'returnTo=%2Fdevice' in link
    assert '#token=reset-token' in link
    assert 'token=' not in link.split('#')[0]


def test_smtp_auth_failures_cannot_echo_action_tokens_into_logs(monkeypatch):
    from server.services import smtp_email_service

    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('SMTP_USE_SSL', 'false')
    client = MagicMock()
    client.sendmail.side_effect = smtplib.SMTPDataError(
        550, b'rejected body containing SECRET-ACTION-TOKEN'
    )
    monkeypatch.setattr(
        smtp_email_service.smtplib, 'SMTP', lambda *args, **kwargs: client
    )
    log = MagicMock()
    monkeypatch.setattr(smtp_email_service, 'logger', log)
    smtp_email_service.SMTPEmailService.send_auth_email(
        'person@example.com',
        'password_reset',
        'https://app.example.com/auth/reset-password#token=SECRET-ACTION-TOKEN',
    )
    log.exception.assert_not_called()
    log.warning.assert_called_once_with('Failed to send account email')


async def test_rate_limit_uses_identifier_and_ip_and_fails_closed(monkeypatch):
    limiter = SimpleNamespace(
        strategy=SimpleNamespace(hit=AsyncMock(return_value=True)),
        limit_items=['minute', 'hour'],
    )
    monkeypatch.setattr(limits, '_limiter', lambda recovery: limiter)
    request = Request({'type': 'http', 'headers': [], 'client': ('192.0.2.10', 5000)})
    await limits.throttle(request, ' Person@EXAMPLE.com ')
    calls = limiter.strategy.hit.call_args_list
    assert len(calls) == 4
    assert calls[0].args[2] == 'identifier' and 'Person' not in calls[0].args[3]
    assert calls[2].args[2:] == ('source', '192.0.2.10')
    limiter.strategy.hit.side_effect = ConnectionError('Redis unavailable')
    with pytest.raises(HTTPException) as error:
        await limits.throttle(request, 'person@example.com')
    assert error.value.status_code == 503
    limiter.strategy.hit.side_effect = None
    limiter.strategy.hit.return_value = False
    with pytest.raises(HTTPException) as error:
        await limits.throttle(request, 'person@example.com')
    assert error.value.status_code == 429
