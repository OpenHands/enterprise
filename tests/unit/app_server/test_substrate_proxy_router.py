"""Tests for the app's proxy to ports on Substrate sandboxes.

Focus areas:
- who may use the proxy: the session key holder, or the signed-in owner
- the app's credentials never reach a sandbox, and a sandbox's cookies and
  redirects stay inside its prefix
- requests and responses pass through, with the prefix stripped
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import SecretStr
from starlette.requests import Request

from openhands.app_server.middleware import (
    InMemoryRateLimiter,
    RateLimitMiddleware,
)
from openhands.app_server.sandbox import substrate_proxy_router as proxy
from openhands.app_server.sandbox.sandbox_store import (
    DOCKER_BACKEND,
    SUBSTRATE_BACKEND,
    LifecycleState,
    StoredSandbox,
    hash_session_api_key,
)

SANDBOX_ID = 'oh-0123456789abcdef01234567'
PREFIX = f'/api/v1/sandbox-proxy/{SANDBOX_ID}/8001'
OWNER_ID = 'user-1'
SESSION_API_KEY = 'the-session-key'


def _request(
    headers: dict[str, str] | None = None,
    query: str = '',
    path: str = f'{PREFIX}/',
) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {
            'type': 'http',
            'method': 'GET',
            'scheme': 'https',
            'path': path,
            'query_string': query.encode(),
            'headers': [(b'host', b'app.example.com'), *raw],
            'client': ('203.0.113.7', 5000),
            'server': ('app.example.com', 443),
        }
    )


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------


class TestForwardHeaders:
    def test_strips_the_apps_credentials(self):
        headers = dict(
            proxy.forward_headers(
                _request(
                    {
                        'Authorization': 'Bearer app-key',
                        'X-Access-Token': 'token',
                        'Cookie': (
                            'openhands_auth=jwt; keycloak_auth=a; keycloak_auth_1=b; '
                            'api_key=k; openhands_editor_abc=editor; theme=dark'
                        ),
                        'X-Session-API-Key': SESSION_API_KEY,
                    }
                ),
                PREFIX,
            )
        )

        assert 'Authorization' not in headers
        assert 'X-Access-Token' not in headers
        assert headers['cookie'] == 'openhands_editor_abc=editor; theme=dark'
        assert headers['x-session-api-key'] == SESSION_API_KEY

    def test_no_cookie_header_when_only_app_cookies(self):
        headers = dict(
            proxy.forward_headers(_request({'Cookie': 'openhands_auth=jwt'}), PREFIX)
        )

        assert 'cookie' not in headers

    def test_drops_hop_by_hop_headers(self):
        headers = dict(
            proxy.forward_headers(
                _request(
                    {
                        'Connection': 'keep-alive, X-Custom-Hop',
                        'X-Custom-Hop': '1',
                        'Keep-Alive': 'timeout=5',
                        'Accept': 'text/html',
                    }
                ),
                PREFIX,
            )
        )

        assert set(headers) == {
            'accept',
            'x-forwarded-host',
            'x-forwarded-proto',
            'x-forwarded-prefix',
            'x-forwarded-for',
        }

    def test_says_where_the_browser_is(self):
        headers = dict(proxy.forward_headers(_request(), PREFIX))

        assert headers['x-forwarded-host'] == 'app.example.com'
        assert headers['x-forwarded-proto'] == 'https'
        assert headers['x-forwarded-prefix'] == PREFIX
        assert headers['x-forwarded-for'] == '203.0.113.7'

    def test_keeps_the_edge_proxys_view(self):
        headers = dict(
            proxy.forward_headers(
                _request(
                    {
                        'X-Forwarded-Host': 'public.example.com',
                        'X-Forwarded-Proto': 'https',
                    }
                ),
                PREFIX,
            )
        )

        assert headers['x-forwarded-host'] == 'public.example.com'


def _upstream(headers: list[tuple[str, str]], status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, headers=headers)


ACTOR_ORIGIN = f'http://{SANDBOX_ID}:8001'


class TestResponseHeaders:
    @pytest.mark.parametrize(
        ('location', 'expected'),
        [
            ('/?folder=/workspace', f'{PREFIX}/?folder=/workspace'),
            (f'{ACTOR_ORIGIN}/login', f'{PREFIX}/login'),
            (ACTOR_ORIGIN, f'{PREFIX}/'),
            ('https://github.com/login', 'https://github.com/login'),
            ('//evil.example.com/x', '//evil.example.com/x'),
            ('relative/path', 'relative/path'),
        ],
    )
    def test_redirects_stay_in_the_prefix(self, location, expected):
        headers = dict(
            proxy.response_headers(
                _upstream([('location', location)], 303), PREFIX, ACTOR_ORIGIN
            )
        )

        assert headers['location'] == expected

    def test_cookies_are_scoped_to_the_prefix(self):
        headers = proxy.response_headers(
            _upstream(
                [
                    ('set-cookie', 'editor=1; Path=/; HttpOnly'),
                    ('set-cookie', 'app=2; path=/static; Domain=oh-1'),
                    ('set-cookie', 'bare=3'),
                ]
            ),
            PREFIX,
            ACTOR_ORIGIN,
        )

        cookies = [value for name, value in headers if name == 'set-cookie']
        assert cookies == [
            f'editor=1; Path={PREFIX}/; HttpOnly',
            f'app=2; Path={PREFIX}/static',
            f'bare=3; Path={PREFIX}/',
        ]

    def test_a_sandbox_cannot_set_an_app_cookie(self):
        headers = proxy.response_headers(
            _upstream(
                [
                    ('set-cookie', 'openhands_auth=forged; Path=/'),
                    ('set-cookie', 'keycloak_auth=forged'),
                ]
            ),
            PREFIX,
            ACTOR_ORIGIN,
        )

        assert [name for name, _ in headers if name == 'set-cookie'] == []

    def test_sets_its_own_policy_unless_the_sandbox_does(self):
        default = dict(proxy.response_headers(_upstream([]), PREFIX, ACTOR_ORIGIN))
        own = dict(
            proxy.response_headers(
                _upstream([('content-security-policy', "default-src 'self'")]),
                PREFIX,
                ACTOR_ORIGIN,
            )
        )

        assert default['content-security-policy'] == "frame-ancestors 'self'"
        assert own['content-security-policy'] == "default-src 'self'"

    def test_drops_headers_the_app_server_sets(self):
        headers = dict(
            proxy.response_headers(
                _upstream(
                    [
                        ('date', 'x'),
                        ('server', 'uvicorn'),
                        ('connection', 'close'),
                        ('content-type', 'text/html'),
                    ]
                ),
                PREFIX,
                ACTOR_ORIGIN,
            )
        )

        assert set(headers) == {'content-type', 'content-security-policy'}


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session(async_session_maker):
    async with async_session_maker() as session:
        yield session


@pytest.fixture
def stored(db_session):
    async def _stored(
        backend: str = SUBSTRATE_BACKEND,
        lifecycle_state: LifecycleState = LifecycleState.RUNNING,
    ) -> StoredSandbox:
        sandbox = StoredSandbox(
            id=SANDBOX_ID,
            backend=backend,
            created_by_user_id=OWNER_ID,
            sandbox_spec_id='openhands-agent-server',
            session_api_key_hash=hash_session_api_key(SESSION_API_KEY),
            session_api_key=SecretStr(SESSION_API_KEY),
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            lifecycle_state=lifecycle_state,
        )
        db_session.add(sandbox)
        await db_session.commit()
        return sandbox

    return _stored


@pytest.fixture
def authorize(db_session):
    """``authorize`` on the test's database, as a caller signed in as ``user``."""

    @asynccontextmanager
    async def _session(state, request=None):
        yield db_session

    async def _authorize(request: Request, user: str | None = None):
        with (
            patch.object(proxy, 'get_db_session', _session),
            patch.object(proxy, '_caller_user_id', AsyncMock(return_value=user)),
        ):
            return await proxy.authorize(request, SANDBOX_ID)

    return _authorize


class TestAuthorize:
    @pytest.mark.asyncio
    async def test_session_key_header(self, stored, authorize):
        await stored()

        sandbox = await authorize(_request({'X-Session-API-Key': SESSION_API_KEY}))

        assert sandbox.id == SANDBOX_ID

    @pytest.mark.asyncio
    async def test_session_key_query_parameter(self, stored, authorize):
        await stored()

        sandbox = await authorize(_request(query=f'session_api_key={SESSION_API_KEY}'))

        assert sandbox.id == SANDBOX_ID

    @pytest.mark.asyncio
    async def test_wrong_session_key(self, stored, authorize):
        await stored()

        with pytest.raises(HTTPException) as exc_info:
            await authorize(_request({'X-Session-API-Key': 'wrong'}), user=OWNER_ID)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_signed_in_owner(self, stored, authorize):
        await stored()

        sandbox = await authorize(_request(), user=OWNER_ID)

        assert sandbox.id == SANDBOX_ID

    @pytest.mark.asyncio
    async def test_other_user_sees_nothing(self, stored, authorize):
        await stored()

        with pytest.raises(HTTPException) as exc_info:
            await authorize(_request(), user='someone-else')

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_not_signed_in(self, stored, authorize):
        await stored()

        with pytest.raises(HTTPException) as exc_info:
            await authorize(_request())

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_paused_sandbox_is_refused(self, stored, authorize):
        """The router would wake it, behind the app's back."""
        await stored(lifecycle_state=LifecycleState.PAUSED)

        with pytest.raises(HTTPException) as exc_info:
            await authorize(_request({'X-Session-API-Key': SESSION_API_KEY}))

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_another_backends_sandbox_is_not_found(self, stored, authorize):
        await stored(backend=DOCKER_BACKEND)

        with pytest.raises(HTTPException) as exc_info:
            await authorize(_request({'X-Session-API-Key': SESSION_API_KEY}))

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Forwarding
# ---------------------------------------------------------------------------


@pytest.fixture
def upstream():
    """The actor, as the router client sees it."""
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = await request.aread()
        # A stream, not content: httpx reads content up front, and the proxy streams.
        return httpx.Response(
            201,
            headers=[
                ('content-type', 'text/plain'),
                ('set-cookie', 'a=1; Path=/'),
                ('set-cookie', 'b=2; Path=/'),
            ],
            stream=httpx.ByteStream(b'echo:' + body),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client, seen


@pytest.fixture
def app(upstream):
    client, _ = upstream
    app = FastAPI()
    app.include_router(proxy.router, prefix='/api/v1')
    with (
        patch.object(proxy, '_injector', return_value=MagicMock()),
        patch.object(proxy, 'authorize', AsyncMock()),
        patch.object(proxy, '_router_client', return_value=client),
    ):
        yield app


async def _call(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url='https://app.example.com'
    ) as client:
        return await client.request(method, path, **kwargs)


class TestForwarding:
    @pytest.mark.asyncio
    async def test_strips_the_prefix_and_passes_the_body(self, app, upstream):
        _, seen = upstream

        response = await _call(
            app,
            'POST',
            f'/api/v1/sandbox-proxy/{SANDBOX_ID}/8000/api/init?x=1',
            content=b'payload',
            headers={'Cookie': 'openhands_auth=jwt; theme=dark'},
        )

        assert response.status_code == 201
        assert response.content == b'echo:payload'
        assert str(seen[0].url) == f'http://{SANDBOX_ID}:8000/api/init?x=1'
        assert seen[0].headers['cookie'] == 'theme=dark'
        assert seen[0].headers['x-forwarded-prefix'] == (
            f'/api/v1/sandbox-proxy/{SANDBOX_ID}/8000'
        )

    @pytest.mark.asyncio
    async def test_bare_port_url_reaches_the_root(self, app, upstream):
        _, seen = upstream

        await _call(app, 'GET', f'/api/v1/sandbox-proxy/{SANDBOX_ID}/8000')

        assert str(seen[0].url) == f'http://{SANDBOX_ID}:8000/'
        assert seen[0].content == b''

    @pytest.mark.asyncio
    async def test_keeps_every_cookie_the_sandbox_sets(self, app):
        response = await _call(app, 'GET', f'/api/v1/sandbox-proxy/{SANDBOX_ID}/8011/')

        prefix = f'/api/v1/sandbox-proxy/{SANDBOX_ID}/8011'
        assert response.headers.get_list('set-cookie') == [
            f'a=1; Path={prefix}/',
            f'b=2; Path={prefix}/',
        ]

    @pytest.mark.asyncio
    async def test_unreachable_actor_is_a_bad_gateway(self, app, upstream):
        client, _ = upstream
        client.send = AsyncMock(side_effect=httpx.ConnectError('tunnel refused'))

        response = await _call(app, 'GET', f'/api/v1/sandbox-proxy/{SANDBOX_ID}/8000/')

        assert response.status_code == 502

    @pytest.mark.asyncio
    async def test_out_of_range_port(self, app):
        response = await _call(app, 'GET', f'/api/v1/sandbox-proxy/{SANDBOX_ID}/70000/')

        assert response.status_code == 404


@pytest.mark.asyncio
async def test_other_backends_have_no_proxy():
    app = FastAPI()
    app.include_router(proxy.router, prefix='/api/v1')
    config = MagicMock(sandbox=MagicMock())

    with patch('openhands.app_server.config.get_global_config', return_value=config):
        response = await _call(app, 'GET', f'/api/v1/sandbox-proxy/{SANDBOX_ID}/8000/')

    assert response.status_code == 404


def test_proxy_traffic_is_not_rate_limited_per_ip():
    middleware = RateLimitMiddleware(MagicMock(), InMemoryRateLimiter())

    assert not middleware.is_rate_limited_request(
        _request(path=f'{PREFIX}/static/x.js')
    )
    assert middleware.is_rate_limited_request(_request(path='/api/v1/sandboxes/search'))
