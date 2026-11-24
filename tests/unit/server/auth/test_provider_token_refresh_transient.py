"""A provider that is briefly unavailable must not cost the user their git link.

Runs ``SaasUserAuth.get_provider_tokens`` against the real token stores on the
test database and the real refresh code. The provider's OAuth token endpoint is
a local HTTP server whose reply each test chooses.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import SecretStr

from openhands.app_server.integrations.service_types import ProviderType
from openhands.app_server.user_auth.user_auth import AuthType
from server.auth import saas_user_auth
from server.auth.auth_error import AuthError
from server.auth.saas_user_auth import SaasUserAuth
from storage.auth_token_store import AuthTokenStore
from storage.encrypt_utils import get_jwt_service
from storage.oauth_token_store import OAuthTokenStore


class _TokenEndpoint(BaseHTTPRequestHandler):
    reply: tuple[int, dict] = (503, {'error': 'temporarily_unavailable'})
    calls = 0

    def do_POST(self):  # noqa: N802
        type(self).calls += 1
        self.rfile.read(int(self.headers['Content-Length']))
        status, body = type(self).reply
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def token_endpoint() -> Iterator[tuple[type[_TokenEndpoint], str]]:
    handler = type('TokenEndpoint', (_TokenEndpoint,), {'calls': 0})
    server = HTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield handler, f'http://127.0.0.1:{server.server_port}'
    server.shutdown()


def _closed_port_url() -> str:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return f'http://127.0.0.1:{s.getsockname()[1]}'


async def _provider_tokens(user_id, *, oauth_v2_cookie: bool = True):
    auth = SaasUserAuth(
        user_id=str(user_id),
        refresh_token=SecretStr(''),
        auth_type=AuthType.COOKIE,
        oauth_v2_cookie=oauth_v2_cookie,
    )
    with patch.object(auth, 'get_secrets', new=AsyncMock(return_value=None)):
        return await auth.get_provider_tokens()


def _token_values(tokens) -> dict[str, str]:
    return {k.value: v.token.get_secret_value() for k, v in (tokens or {}).items()}


_EXPIRED = datetime.now(timezone.utc) - timedelta(minutes=5)


@pytest.mark.asyncio
async def test_v2_provider_503_keeps_the_link_and_the_next_request_recovers(
    token_endpoint, create_git_provider, store_oauth_token, create_user
):
    endpoint, host = token_endpoint
    gitlab = await create_git_provider(ProviderType.GITLAB, host=host)
    github = await create_git_provider(ProviderType.GITHUB)
    user = create_user()
    await store_oauth_token(
        user.id, gitlab, 'glpat-expired', access_token_expires_at=_EXPIRED
    )
    await store_oauth_token(user.id, github, 'gho-valid')
    gitlab_store = OAuthTokenStore(user_id=user.id, oauth_provider_id=gitlab.id)

    endpoint.reply = (503, {'error': 'temporarily_unavailable'})
    during_outage = await _provider_tokens(user.id)

    assert _token_values(during_outage) == {'github': 'gho-valid'}
    assert await gitlab_store.get_raw() is not None

    endpoint.reply = (
        200,
        {'access_token': 'glpat-new', 'refresh_token': 'r2', 'expires_in': 7200},
    )
    after_recovery = await _provider_tokens(user.id)

    assert _token_values(after_recovery) == {
        'github': 'gho-valid',
        'gitlab': 'glpat-new',
    }
    assert endpoint.calls == 2


@pytest.mark.asyncio
async def test_v2_unreachable_token_endpoint_keeps_the_link(
    create_git_provider, store_oauth_token, create_user
):
    gitlab = await create_git_provider(ProviderType.GITLAB, host=_closed_port_url())
    user = create_user()
    await store_oauth_token(
        user.id, gitlab, 'glpat-expired', access_token_expires_at=_EXPIRED
    )

    assert _token_values(await _provider_tokens(user.id)) == {}
    assert (
        await OAuthTokenStore(user_id=user.id, oauth_provider_id=gitlab.id).get_raw()
        is not None
    )


@pytest.mark.asyncio
async def test_v2_rejected_refresh_token_removes_the_link(
    token_endpoint, create_git_provider, store_oauth_token, create_user
):
    endpoint, host = token_endpoint
    gitlab = await create_git_provider(ProviderType.GITLAB, host=host)
    user = create_user()
    await store_oauth_token(
        user.id, gitlab, 'glpat-expired', access_token_expires_at=_EXPIRED
    )

    endpoint.reply = (400, {'error': 'invalid_grant'})
    with pytest.raises(AuthError):
        await _provider_tokens(user.id)

    assert (
        await OAuthTokenStore(user_id=user.id, oauth_provider_id=gitlab.id).get_raw()
        is None
    )


async def _store_expired_legacy_gitlab_token(user_id) -> AuthTokenStore:
    jwt_svc = get_jwt_service()
    store = await AuthTokenStore.get_instance(
        keycloak_user_id=str(user_id), idp=ProviderType.GITLAB
    )
    await store.store_tokens(
        access_token=jwt_svc.encrypt_value('glpat-old'),
        refresh_token=jwt_svc.encrypt_value('refresh'),
        access_token_expires_at=int(_EXPIRED.timestamp()),
        refresh_token_expires_at=0,
    )
    return store


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('reply', 'row_kept'),
    [
        ((503, {'error': 'unavailable'}), True),
        ((400, {'error': 'invalid_grant'}), False),
    ],
)
async def test_legacy_auth_tokens_row_survives_only_transient_errors(
    oauth_v2_stores, token_endpoint, create_user, reply, row_kept
):
    endpoint, host = token_endpoint
    endpoint.reply = reply
    user = create_user()
    store = await _store_expired_legacy_gitlab_token(user.id)

    with (
        patch.object(saas_user_auth.token_manager, '_jwt_svc', get_jwt_service()),
        patch('server.auth.token_manager.GITLAB_TOKEN_URL', f'{host}/oauth/token'),
    ):
        if row_kept:
            assert (
                _token_values(await _provider_tokens(user.id, oauth_v2_cookie=False))
                == {}
            )
        else:
            with pytest.raises(AuthError):
                await _provider_tokens(user.id, oauth_v2_cookie=False)

    assert endpoint.calls == 1
    assert (await store.load_tokens() is not None) is row_kept


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request('POST', 'https://idp.example.com/token')
    return httpx.HTTPStatusError(
        'x', request=request, response=httpx.Response(code, request=request)
    )


@pytest.mark.parametrize(
    ('error', 'transient'),
    [
        (httpx.ConnectError('refused'), True),
        (httpx.ReadTimeout('slow'), True),
        (_status_error(503), True),
        (_status_error(429), True),
        (_status_error(400), False),
        (_status_error(401), False),
        (ValueError('Both Access and Refresh Tokens expired.'), False),
    ],
)
def test_is_transient_refresh_error_follows_the_cause_chain(error, transient):
    try:
        raise ValueError('Failed to get token') from error
    except ValueError as wrapped:
        assert saas_user_auth._is_transient_refresh_error(wrapped) is transient
